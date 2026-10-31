# backend/app/employee_import.py — Mining Excel importer engine (Batch 4).
#
# The browser parses the workbook and sends normalised rows (lists of cell
# values) plus the column mapping; everything here re-validates from those
# values and never trusts the browser's interpretation. plan_import() only
# reads (preview); commit_import() re-plans on fresh reads, writes, then
# logs the import to employee_imports; undo_import() deletes what a commit
# created through the normal delete path, so Recently deleted keeps them.
#
# Contract with the frontend:
# - mapping: {str(col_index): target | {"target": ..., "label": ...}} where
#   target is a person field (mine_number, first_name, last_name, full_name,
#   job_title, section, date_engaged, id_number, phone), a wide PPE column
#   (ppe:<type>), its size column (size:<type>), a long-layout column
#   (item_text, size, issue_date), or ignore. A ppe column's label (the
#   header text) becomes the record's item_name, else the type code.
# - layout "wide": one row per person, PPE columns hold issue dates.
#   "long": one row per issue (person columns + item_text + size + date).
# - Every row is stamped department (default Mining); section comes from the
#   file, or the department when absent. Blank departments stay Engineering
#   and are never created or matched here.
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.db_helpers import fetch_all_pages
from app.ppe_conflicts import find_duplicate
from app.scope import SEPARATE_DEPARTMENTS

logger = logging.getLogger(__name__)

MAX_ROWS = 20000

PERSON_TARGETS = frozenset({
    "mine_number", "first_name", "last_name", "full_name", "job_title",
    "section", "date_engaged", "id_number", "phone",
})

# Row fields the importer may fill on an existing Mining person (blank only;
# differences are listed, never overwritten). Mine number is the identity and
# is never changed; names are never overwritten either (only set on create).
FILLABLE = ("first_name", "last_name", "job_title", "section",
            "date_engaged", "id_number", "phone")

COLUMN_OF = {
    "first_name": "first_name", "last_name": "last_name",
    "job_title": "designation", "section": "section",
    "date_engaged": "date_of_engagement", "id_number": "id_number",
    "phone": "phone",
}


class PlanError(ValueError):
    """A request-level problem (bad mapping, unknown type): the router answers 422."""


def canonical_department(name: Any) -> str:
    """The separate department `name` names (case-insensitive), or raise."""
    want = str(name or "").strip()
    for separate in SEPARATE_DEPARTMENTS:
        if want.lower() == separate.lower():
            return separate
    raise PlanError(f"Unknown department '{want}'. Allowed: {', '.join(SEPARATE_DEPARTMENTS)}.")


def _text(value: Any) -> str:
    """A cell to stripped text. Integral floats render without the ".0" so an
    Excel mine number typed as a number still matches the register."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _blank(value: Any) -> bool:
    return _text(value) == "" and not isinstance(value, (datetime, date))


def normalize_type(name: Any) -> str:
    """Loose PPE-type match (case and surrounding space); display stays raw."""
    return (name or "").strip().lower() if isinstance(name, str) else ""


def parse_import_date(value: Any) -> tuple[Optional[str], bool]:
    """A cell to (ISO date, approximate). Excel serials, dd/mm/yyyy (and
    dd-mm-yyyy, yyyy/mm/dd), ISO, and "Oct 2025" (first of the month,
    approximate). Anything else — including a non-blank unparseable value —
    is (None, False); the caller tells blank apart from unreadable."""
    if value is None or _blank(value):
        return None, False
    if isinstance(value, datetime):
        return value.date().isoformat(), False
    if isinstance(value, date):
        return value.isoformat(), False
    if isinstance(value, bool):
        return None, False
    if isinstance(value, (int, float)):
        try:
            day = date(1899, 12, 30) + timedelta(days=int(value))
        except (OverflowError, ValueError):
            return None, False
        if date(1990, 1, 1) <= day <= date(2100, 1, 1):
            return day.isoformat(), False
        return None, False
    text = _text(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat(), False
        except ValueError:
            continue
    for fmt in ("%b %Y", "%B %Y", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt).date().replace(day=1).isoformat(), True
        except ValueError:
            continue
    return None, False


def split_full_name(value: Any) -> tuple[str, str]:
    """(first, last) from one "SURNAME Firstname" cell: a leading all-caps
    token is the surname; otherwise the last token is. A lone all-caps token
    is a surname with no first name; a lone mixed-case token is a first name."""
    tokens = _text(value).split()
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return ("", tokens[0]) if tokens[0].isupper() else (tokens[0], "")
    if tokens[0].isupper() and any(not t.isupper() for t in tokens[1:]):
        return " ".join(tokens[1:]), tokens[0]
    if all(t.isupper() for t in tokens):
        return " ".join(tokens[1:]), tokens[0]
    return " ".join(tokens[:-1]), tokens[-1]


def _normalize_mapping(mapping: Any) -> dict[int, dict]:
    """{col: {"target", "label"}} or raise PlanError. Column keys must be
    integers; every target must be a known person field, ppe:<type>,
    size:<type>, item_text, size, issue_date, or ignore."""
    if not isinstance(mapping, dict) or not mapping:
        raise PlanError("The column mapping is missing.")
    out: dict[int, dict] = {}
    for raw_col, spec in mapping.items():
        try:
            col = int(str(raw_col))
        except (TypeError, ValueError):
            raise PlanError(f"Column '{raw_col}' is not a number.")
        if isinstance(spec, dict):
            target, label = spec.get("target"), spec.get("label") or spec.get("header")
        elif isinstance(spec, str):
            target, label = spec, None
        else:
            raise PlanError(f"Column {col} maps to nothing readable.")
        target = (target or "").strip()
        low = target.lower()
        if target in PERSON_TARGETS or target == "ignore" or low in ("item_text", "size", "issue_date"):
            out[col] = {"target": low if low in ("item_text", "size", "issue_date") else target,
                        "label": _text(label) if label else None}
        elif low.startswith("ppe:") and target[4:].strip():
            out[col] = {"target": "ppe", "code": target[4:].strip(), "label": _text(label) if label else None}
        elif low.startswith("size:") and target[5:].strip():
            out[col] = {"target": "size", "code": target[5:].strip(), "label": None}
        else:
            raise PlanError(f"Column {col} maps to unknown target '{target}'.")
    return out


def _live_matrix(db: Any) -> dict:
    """Saved ppe_matrix rows, or {} when the table is unreachable (the
    defaults still apply; a missing matrix must not block an import)."""
    try:
        resp = db.table("ppe_matrix").select("ppe_type, interval_months").execute()
        return {r["ppe_type"]: r["interval_months"] for r in (resp.data or [])}
    except Exception as e:
        logger.info(f"ppe_matrix unavailable for import, using defaults: {e}")
        return {}


def _known_types(db: Any) -> tuple[dict, dict]:
    """({normalized: canonical code}, {normalized: interval_months}): the saved
    matrix over the server defaults, plus every type live PPE records use
    (e.g. boots), so a mapped column to a type people actually hold is never
    refused as unknown. Live record spellings only fill gaps the vocabulary
    does not name."""
    from app.routers.ppe import PPE_MATRIX_DEFAULTS
    canonical = {normalize_type(k): k for k in PPE_MATRIX_DEFAULTS}
    intervals: dict[str, Optional[int]] = {normalize_type(k): v for k, v in PPE_MATRIX_DEFAULTS.items()}
    for k, v in _live_matrix(db).items():
        canonical.setdefault(normalize_type(k), k)
        intervals[normalize_type(k)] = v
    try:
        resp = db.table("ppe_records").select("ppe_type").execute()
        for r in (resp.data or []):
            code = normalize_type(r.get("ppe_type"))
            if code and code not in canonical:
                canonical[code] = (r.get("ppe_type") or "").strip()
                intervals.setdefault(code, None)
    except Exception as e:
        logger.info(f"ppe_records types unavailable for import: {e}")
    return canonical, intervals


def _expiry_for(ppe_type: str, issue_iso: str, intervals: dict) -> Optional[str]:
    """issue date + matrix interval, like the PPE form implies: interval 0 or
    unknown means no expiry, never a guessed date."""
    from app.routers.ppe import _add_months
    months = intervals.get(normalize_type(ppe_type))
    if not months:
        return None
    try:
        return _add_months(issue_iso, int(months))
    except (TypeError, ValueError):
        return None


def _check_columns(mapped: dict[int, dict], layout: str, known: dict) -> None:
    """One mine_number; no clashing person names; size columns pair with a PPE
    column of the same code; long rows name their item and date exactly once;
    every mapped code is known."""
    singles = [c for c, m in mapped.items() if m["target"] == "mine_number"]
    if len(singles) != 1:
        raise PlanError("Map exactly one column to the mine number.")
    by_target: dict[str, list] = {}
    for c, m in mapped.items():
        by_target.setdefault(m["target"], []).append(c)
    if "full_name" in by_target and ("first_name" in by_target or "last_name" in by_target):
        raise PlanError("Map a full name or first + surname, not both.")
    ppe_codes = {normalize_type(m["code"]) for c, m in mapped.items() if m["target"] == "ppe"}
    for c, m in mapped.items():
        if m["target"] != "size" or "code" not in m:
            continue
        if normalize_type(m["code"]) not in ppe_codes:
            raise PlanError(f"Size column {c} pairs with no PPE column for '{m['code']}'.")
    unknown = sorted({m["code"] for c, m in mapped.items()
                      if m["target"] in ("ppe", "size") and "code" in m
                      and normalize_type(m["code"]) not in known})
    if unknown:
        raise PlanError(f"Unknown PPE type(s): {', '.join(unknown)}.")
    if layout == "wide" and any(m["target"] == "size" and "code" not in m for m in mapped.values()):
        raise PlanError("Wide layout sizes pair per item (size:<type>), not a bare size column.")
    if layout == "long":
        for need in ("item_text", "issue_date"):
            if len(by_target.get(need, [])) != 1:
                raise PlanError(f"Long layout needs exactly one {need.replace('_', ' ')} column.")


def _cell(row: Any, col: int) -> Any:
    """The cell value, or None past the row's end (ragged rows are normal)."""
    if isinstance(row, (list, tuple)) and 0 <= col < len(row):
        return row[col]
    return None


def _row_blank(row: Any, mapped: dict[int, dict]) -> bool:
    return all(_blank(_cell(row, c)) for c in mapped)


def _person_fields(row: Any, mapped: dict[int, dict]) -> dict:
    """The mapped person values as plain strings (dates parsed): mine_number,
    first_name, last_name, job_title, section, date_engaged, id_number, phone.
    full_name splits here, so one column feeds both names."""
    fields: dict[str, str] = {}
    for col, m in mapped.items():
        target = m["target"]
        if target == "mine_number":
            fields["mine_number"] = _text(_cell(row, col))
        elif target in ("first_name", "last_name", "job_title", "section", "id_number", "phone"):
            value = _text(_cell(row, col))
            if value:
                fields[target] = value
        elif target == "full_name":
            first, last = split_full_name(_cell(row, col))
            if first:
                fields.setdefault("first_name", first)
            if last:
                fields.setdefault("last_name", last)
        elif target == "date_engaged":
            iso, _approx = parse_import_date(_cell(row, col))
            if iso:
                fields["date_engaged"] = iso
    return fields


def _wide_issues(row: Any, mapped: dict[int, dict]) -> list[dict]:
    """One entry per PPE column with a readable date: {code, item_name, size,
    issue_iso, approximate, bad_date}. Unreadable non-blank cells are flagged
    here, refused by the caller."""
    sizes = {normalize_type(m["code"]): _text(_cell(row, c))
             for c, m in mapped.items() if m["target"] == "size" and "code" in m}
    out = []
    for col, m in sorted(mapped.items()):
        if m["target"] != "ppe":
            continue
        cell = _cell(row, col)
        if _blank(cell):
            continue
        iso, approx = parse_import_date(cell)
        out.append({"ppe_type": m["code"], "item_name": m["label"] or m["code"],
                    "size": sizes.get(normalize_type(m["code"]), "") or None,
                    "issue_iso": iso, "approximate": approx,
                    "bad_date": None if iso else _text(cell)})
    return out


def _long_issues(row: Any, mapped: dict[int, dict], canonical: dict) -> list[dict]:
    """Long layout: the item_text cell names the type (kept raw + flagged when
    unknown), the size cell is the size, the issue_date cell is the date."""
    item_col = next(c for c, m in mapped.items() if m["target"] == "item_text")
    date_col = next(c for c, m in mapped.items() if m["target"] == "issue_date")
    bare = [c for c, m in mapped.items() if m["target"] == "size" and "code" not in m]
    size_cols = bare or [c for c, m in mapped.items() if m["target"] == "size"]
    raw_item = _text(_cell(row, item_col))
    if not raw_item:
        return []
    match = canonical.get(normalize_type(raw_item))
    date_cell = _cell(row, date_col)
    iso, approx = parse_import_date(date_cell)
    size = _text(_cell(row, size_cols[0])) if size_cols else ""
    return [{"ppe_type": match or raw_item, "item_name": raw_item,
             "unknown_item": match is None,
             "size": size or None, "issue_iso": iso, "approximate": approx,
             "bad_date": None if iso else _text(date_cell)}]


REGISTER_COLUMNS = ("id,employee_id,department,first_name,last_name,designation,"
                    "section,date_of_engagement,id_number,phone")


def _load_register(db: Any) -> tuple[dict, list]:
    """(by_upper_number, ambiguous_numbers). Raises loudly on read failure: an
    unread register must stop the import, never silently create duplicates."""
    resp = fetch_all_pages(
        lambda start, end: db.table("employees").select(REGISTER_COLUMNS).range(start, end).execute())
    by_number: dict[str, list] = {}
    for row in resp:
        number = (row.get("employee_id") or "").strip().upper()
        if number:
            by_number.setdefault(number, []).append(row)
    ambiguous = sorted(n for n, rows in by_number.items() if len(rows) > 1)
    return by_number, ambiguous


def _load_live_ppe(db: Any, numbers: list) -> list:
    """Live PPE rows for these mine numbers, for the duplicate check."""
    if not numbers:
        return []
    try:
        resp = db.table("ppe_records").select(
            "id,employee_id,ppe_type,issue_date,status").in_("employee_id", sorted(set(numbers))).execute()
        return list(resp.data or [])
    except Exception as e:
        raise PlanError(f"Could not read existing PPE records: {e}.")


def _display_name(row: dict) -> str:
    return " ".join(f"{row.get('first_name') or ''} {row.get('last_name') or ''}".split()) or "?"


def plan_import(db: Any, rows: Any, mapping: Any, layout: str = "wide",
               department: Any = "Mining") -> dict:
    """Dry-run an import: no writes. Returns people/ppe entries with per-row
    actions, counts, problems, and unknown items. Raises PlanError (→422) on
    request-level problems. Reading the register or live PPE failing loudly
    is deliberate: guessing either way creates duplicates or drops issues."""
    dept = canonical_department(department)
    if layout not in ("wide", "long"):
        raise PlanError("Layout is wide or long.")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise PlanError(f"Send at most {MAX_ROWS} rows.")
    mapped = _normalize_mapping(mapping)
    canonical, intervals = _known_types(db)
    _check_columns(mapped, layout, canonical)
    by_number, ambiguous = _load_register(db)

    people: dict[str, dict] = {}
    order: list[str] = []
    ppe_entries: list[dict] = []
    problems: list[dict] = []
    unknown_items: list[dict] = []
    counts = {"rows_seen": 0, "rows_ignored": 0, "people_new": 0, "people_filled": 0,
              "people_unchanged": 0, "people_refused": 0, "ppe_new": 0,
              "ppe_duplicates_skipped": 0, "ppe_refused": 0, "unknown_items": 0}

    row_no = 0
    pending_numbers: list[str] = []
    staged: list[tuple] = []
    for row in rows:
        row_no += 1
        counts["rows_seen"] += 1
        if _row_blank(row, mapped):
            counts["rows_ignored"] += 1
            continue
        fields = _person_fields(row, mapped)
        number = fields.get("mine_number", "")
        issues = (_wide_issues(row, mapped) if layout == "wide"
                  else _long_issues(row, mapped, canonical))
        if not number:
            if issues or any(fields.get(k) for k in FILLABLE if k != "mine_number"):
                reason = (f"Row {row_no}: no mine number — "
                          "a row with a name or issue dates but no number cannot be imported.")
                problems.append({"row": row_no, "reason": reason})
                counts["people_refused"] += 1
                for issue in issues:
                    ppe_entries.append({"row": row_no, "mine_number": "",
                                        "action": "refused", "reason": reason, **issue})
                    counts["ppe_refused"] += 1
            else:
                counts["rows_ignored"] += 1
            continue
        has_names = bool(fields.get("first_name") or fields.get("last_name"))
        has_valid_issues = any(i.get("issue_iso") for i in issues)
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]*$", number) or len(number) > 30:
            if not has_names and not has_valid_issues:
                # A title row ("MINING REGISTER 2025" in the number column):
                # nothing to import, nothing to refuse.
                counts["rows_ignored"] += 1
                continue
            reason = f"Row {row_no}: '{number}' is not a valid mine number."
            problems.append({"row": row_no, "number": number, "reason": reason})
            counts["people_refused"] += 1
            entry = {"row": row_no, "mine_number": number, "action": "refused",
                     "refuse_reason": reason, **{k: fields.get(k, "") for k in FILLABLE}}
            if number.upper() not in people:
                people[number.upper()] = entry
                order.append(number.upper())
            for issue in issues:
                ppe_entries.append({"row": row_no, "mine_number": number, "action": "refused",
                                    "reason": reason, **issue})
                counts["ppe_refused"] += 1
            continue
        pending_numbers.append(number)
        staged.append((row_no, number, fields, issues))

    live_ppe = _load_live_ppe(db, pending_numbers)
    seen_issues: set[tuple] = set()

    # People are decided once per mine number (first row wins): long-layout
    # files repeat a person on every issue row, and that must not multiply
    # the people counts. PPE rows are still processed per row below.
    for row_no, number, fields, _issues in staged:
        key = number.upper()
        if key in people:
            continue
        existing = None if key in ambiguous else (by_number.get(key) or [None])[0]
        if key in ambiguous:
            reason = f"Row {row_no}: {number} matches several register rows \u2014 fix the register first."
            problems.append({"row": row_no, "number": number, "reason": reason})
            counts["people_refused"] += 1
            entry = {"row": row_no, "mine_number": number, "action": "refused",
                     "refuse_reason": reason, **{k: fields.get(k, "") for k in FILLABLE}}
        elif existing is not None and (existing.get("department") or "") != dept:
            if (existing.get("department") or "").strip():
                reason = (f"{number} is already {_display_name(existing)} "
                          f"in {existing.get('department')}.")
            else:
                reason = f"{number} is already {_display_name(existing)} in Engineering."
            problems.append({"row": row_no, "number": number, "reason": reason})
            counts["people_refused"] += 1
            entry = {"row": row_no, "mine_number": number, "action": "refused",
                     "refuse_reason": reason, **{k: fields.get(k, "") for k in FILLABLE}}
        elif existing is not None:
            fill, diffs = {}, []
            for field in FILLABLE:
                column = COLUMN_OF[field]
                have = existing.get(column)
                have_text = "" if have is None else str(have).strip()
                want = (fields.get(field) or "").strip()
                if not want:
                    continue
                if not have_text:
                    fill[field] = want
                elif field == "date_engaged" and have_text[:10] != want[:10]:
                    diffs.append({"field": field, "was": have_text, "filed": want})
                elif field != "date_engaged" and have_text.lower() != want.lower():
                    diffs.append({"field": field, "was": have_text, "filed": want})
            action = "fill" if fill else "unchanged"
            counts["people_filled" if fill else "people_unchanged"] += 1
            entry = {"row": row_no, "mine_number": number, "action": action,
                     "person_id": existing.get("id"), "fill": fill, "diffs": diffs,
                     **{k: fields.get(k, "") for k in FILLABLE}}
        elif not fields.get("first_name") and not fields.get("last_name"):
            reason = f"Row {row_no}: {number} is not on the register and has no name."
            problems.append({"row": row_no, "number": number, "reason": reason})
            counts["people_refused"] += 1
            entry = {"row": row_no, "mine_number": number, "action": "refused",
                     "refuse_reason": reason, **{k: fields.get(k, "") for k in FILLABLE}}
        else:
            counts["people_new"] += 1
            entry = {"row": row_no, "mine_number": number, "action": "create",
                     "person_id": None, "fill": {}, "diffs": [],
                     **{k: fields.get(k, "") for k in FILLABLE}}
        people[key] = entry
        order.append(key)

    seen_issues: set[tuple] = set()
    for row_no, number, fields, issues in staged:
        key = number.upper()
        entry = people[key]
        person_ok = entry["action"] in ("create", "fill", "unchanged")
        for issue in issues:
            base = {"row": row_no, "mine_number": number}
            if not person_ok:
                ppe_entries.append({**base, "action": "refused",
                                    "reason": entry.get("refuse_reason", ""), **issue})
                counts["ppe_refused"] += 1
                continue
            if issue.get("bad_date"):
                reason = (f"Row {row_no}: '{issue['bad_date']}' is not a readable "
                          f"issue date for {issue['item_name']}.")
                ppe_entries.append({**base, "action": "refused", "reason": reason, **issue})
                counts["ppe_refused"] += 1
                continue
            dup_key = (key, normalize_type(issue["ppe_type"]), (issue["issue_iso"] or "")[:10])
            live = find_duplicate([r for r in live_ppe
                                   if (r.get("employee_id") or "").strip().upper() == key],
                                  issue["ppe_type"], issue["issue_iso"])
            if live is not None or dup_key in seen_issues:
                ppe_entries.append({**base, "action": "duplicate_skipped", **issue,
                                    "expiry_date": None})
                counts["ppe_duplicates_skipped"] += 1
                continue
            seen_issues.add(dup_key)
            if issue.get("unknown_item"):
                unknown_items.append({"row": row_no, "value": issue["item_name"]})
                counts["unknown_items"] += 1
            ppe_entries.append({**base, "action": "add", **issue,
                                "expiry_date": _expiry_for(issue["ppe_type"], issue["issue_iso"], intervals)})
            counts["ppe_new"] += 1

    return {"department": dept, "layout": layout, "counts": counts,
            "people": [people[n] for n in order], "ppe": ppe_entries,
            "problems": problems, "unknown_items": unknown_items}


def _person_insert(entry: dict, dept: str) -> dict:
    """A new register row: stamped department, section from the file or the
    department, blanks staying blank for the Mining user to fill in later."""
    fields = {k: entry.get(k, "") for k in FILLABLE}
    return {
        "employee_id": entry["mine_number"],
        "first_name": fields.get("first_name", ""),
        "last_name": fields.get("last_name", ""),
        "designation": fields.get("job_title", ""),
        "department": dept,
        "section": fields.get("section") or dept,
        "date_of_engagement": fields.get("date_engaged") or None,
        "id_number": fields.get("id_number", ""),
        "phone": fields.get("phone") or None,
        "archived": False,
    }


def _person_update(fill: dict) -> dict:
    """Blank-filling patch for an existing Mining person (field names to the
    register columns). Never called with an empty fill."""
    return {COLUMN_OF[field]: value for field, value in fill.items()}


def _ppe_insert(issue: dict, person: dict, dept: str) -> dict:
    """A ppe_records row through the same rules as the PPE form: the register
    stamps the person copies, expiry comes from the matrix, status starts
    active. The duplicate check ran in the plan and runs again at write time."""
    name = " ".join(f"{person.get('first_name') or ''} {person.get('last_name') or ''}".split())
    return {
        "employee_id": person.get("employee_id"),
        "employee_name": name,
        "department": dept,
        "position": person.get("designation") or "",
        "mine_section": person.get("section") or "",
        "ppe_type": issue["ppe_type"],
        "item_name": issue.get("item_name") or issue["ppe_type"],
        "size": issue.get("size"),
        "issue_date": issue["issue_iso"],
        "expiry_date": issue.get("expiry_date"),
        "status": "active",
        "condition": "good",
        "premature_failure": False,
    }


def _records_that_use(db: Any, employee_id: str) -> list:
    """Tables that still hold records for this person (empty when none). Kept
    next to the importer (not reused from the employees router) so the module
    under test owns its database handle."""
    used = []
    for table in ("overtime", "leaves", "ppe_records"):
        try:
            found = db.table(table).select("id").eq("employee_id", employee_id).limit(1).execute()
        except Exception as e:
            raise PlanError(f"Could not check {table} for {employee_id}: {e}.")
        if found.data:
            used.append(table)
    return used


def _person_by_id(db: Any, person_id: Any) -> Optional[dict]:
    if person_id is None:
        return None
    resp = db.table("employees").select(REGISTER_COLUMNS).eq("id", person_id).execute()
    rows = list(resp.data or [])
    return rows[0] if rows else None


def commit_import(db: Any, rows: Any, mapping: Any, layout: str = "wide",
                 department: Any = "Mining", file_name: str = "",
                 created_by: str = "") -> dict:
    """Re-plan on fresh reads, then write: create people, fill blanks, insert
    PPE (duplicates re-checked at write time), log to employee_imports.
    Returns the log summary. Raises PlanError (→422) like the preview."""
    plan = plan_import(db, rows, mapping, layout, department)
    dept = plan["department"]
    created_employee_ids: list = []
    created_ppe_ids: list = []
    counts = {"people_created": 0, "people_filled": 0, "ppe_created": 0,
              "ppe_duplicates_skipped": 0}

    number_to_person: dict[str, dict] = {}
    for person in plan["people"]:
        action = person["action"]
        if action == "create":
            resp = db.table("employees").insert(_person_insert(person, dept)).execute()
            data = list(resp.data or [])
            if not data:
                raise PlanError(f"Creating {person['mine_number']} returned no row.")
            created_employee_ids.append(data[0].get("id"))
            number_to_person[person["mine_number"].upper()] = data[0]
            counts["people_created"] += 1
        elif action == "fill":
            if person.get("fill"):
                db.table("employees").update(_person_update(person["fill"])).eq(
                    "id", person.get("person_id")).execute()
                counts["people_filled"] += 1
            number_to_person[person["mine_number"].upper()] = dict(
                _person_by_id(db, person.get("person_id")) or {})
        elif action == "unchanged":
            number_to_person[person["mine_number"].upper()] = dict(
                _person_by_id(db, person.get("person_id")) or {})

    for issue in plan["ppe"]:
        if issue["action"] != "add":
            if issue["action"] == "duplicate_skipped":
                counts["ppe_duplicates_skipped"] += 1
            continue
        person = number_to_person.get((issue.get("mine_number") or "").upper())
        if person is None:
            continue
        live = _load_live_ppe(db, [person.get("employee_id")])
        if find_duplicate([r for r in live
                           if (r.get("employee_id") or "").strip().upper()
                           == (person.get("employee_id") or "").strip().upper()],
                          issue["ppe_type"], issue["issue_iso"]) is not None:
            counts["ppe_duplicates_skipped"] += 1
            continue
        resp = db.table("ppe_records").insert(_ppe_insert(issue, person, dept)).execute()
        data = list(resp.data or [])
        if data and data[0].get("id") is not None:
            created_ppe_ids.append(data[0].get("id"))
            counts["ppe_created"] += 1

    log = {"department": dept, "file_name": file_name or "",
           "created_by": created_by or None,
           "counts": {**plan["counts"], **counts},
           "created_employee_ids": created_employee_ids,
           "created_ppe_ids": created_ppe_ids}
    resp = db.table("employee_imports").insert(log).execute()
    logged = list(resp.data or [])
    return {"import_id": (logged[0].get("id") if logged else None), **log,
            "refused_people": [p for p in plan["people"] if p["action"] == "refused"],
            "refused_ppe": [p for p in plan["ppe"] if p["action"] == "refused"]}


def undo_import(db: Any, import_id: str) -> dict:
    """Delete what a commit created: this import's PPE rows first, then its
    people — through the normal delete path, so Recently deleted keeps them.
    People who have since gained other records are kept, and listed. Already
    undone is a no-op success. Raises PlanError when the log row is missing."""
    found = db.table("employee_imports").select("*").eq("id", import_id).execute()
    rows = list(found.data or [])
    if not rows:
        raise PlanError(f"Import {import_id} not found.")
    log = rows[0]
    if log.get("undone_at"):
        return {"import_id": import_id, "already_undone": True,
                "removed_people": [], "removed_ppe": [], "kept_people": []}

    removed_ppe, removed_people, kept = [], [], []
    for ppe_id in (log.get("created_ppe_ids") or []):
        existing = db.table("ppe_records").select("id").eq("id", ppe_id).execute()
        if list(existing.data or []):
            db.table("ppe_records").delete().eq("id", ppe_id).execute()
            removed_ppe.append(ppe_id)

    for person_id in (log.get("created_employee_ids") or []):
        prow = db.table("employees").select("id,employee_id,first_name,last_name").eq(
            "id", person_id).execute()
        people = list(prow.data or [])
        if not people:
            continue
        number = people[0].get("employee_id")
        tables = _records_that_use(db, number)
        if tables:
            kept.append({"person_id": person_id, "mine_number": number,
                         "name": _display_name(people[0]), "tables": tables})
            continue
        db.table("employees").delete().eq("id", person_id).execute()
        removed_people.append(person_id)

    db.table("employee_imports").update(
        {"undone_at": datetime.now(timezone.utc).isoformat()}).eq("id", import_id).execute()
    return {"import_id": import_id, "already_undone": False,
            "removed_people": removed_people, "removed_ppe": removed_ppe,
            "kept_people": kept}
