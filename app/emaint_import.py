# app/emaint_import.py — M7: the rules for importing eMaint assets and PM
# schedules. Pure (no DB, no framework): the router reads files and rows, this
# module decides what each row means. The eMaint export file is not available
# yet, so the column headers below are ASSUMED from eMaint's usual asset/PM
# exports (see M7_REPORT.md); every field accepts documented aliases so the
# real file works without renaming.
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

# A mine's asset register fits comfortably below this; above it the file is
# almost certainly the wrong one, and previewing it row by row helps nobody.
MAX_IMPORT_ROWS = 2000


def _norm_header(text: Any) -> str:
    """One header spelling: lowercase, letters and digits only.

    "Asset ID", "AssetID", "asset_id" and "ASSET  ID" all become "assetid",
    so alias matching never depends on spacing, case or underscores."""
    return "".join(c for c in str(text or "").lower() if c.isalnum())


# field key -> (template header, aliases, required, help shown on the page)
ASSET_FIELDS: dict[str, tuple[str, tuple[str, ...], bool, str]] = {
    "asset_id": ("Asset ID", ("AssetID", "Asset Code", "Equipment ID", "EquipmentID", "Code"), False,
                 "eMaint's key for the asset; becomes the register's Equipment ID. Rows without one get an EQ- number."),
    "name": ("Asset Description", ("Description", "Asset Name", "Name", "Asset", "Equipment Name"), True,
             "The machine's name on the register. Required."),
    "category": ("Asset Type", ("Type", "Category", "Class", "Asset Category"), False,
                 "Grouping such as Crushers or Pumps."),
    "manufacturer": ("Manufacturer", ("Mfr", "Make", "Manufacturer Name"), False,
                     "Who made it. Held back until the register grows a Manufacturer column (stored nowhere yet)."),
    "model": ("Model", ("Model No", "Model Number", "Model Name"), False, "Model name or number."),
    "serial_number": ("Serial Number", ("Serial No", "Serial", "SerialNo"), False, "Serial number or VIN."),
    "location": ("Location", ("Site", "Building", "Area", "Asset Location"), False, "Where the machine sits."),
    "department": ("Department", ("Dept",), False, "Owning department, e.g. Engineering."),
    "status": ("Status", ("Asset Status", "Equipment Status"), False,
              "One of: Active, Under Maintenance, Out of Service, Retired, Standby. Blank means Active."),
    "criticality": ("Criticality", ("Critical", "Criticality Rating"), False,
                    "High, Medium or Low. Held back until the register grows a Criticality column (stored nowhere yet)."),
    "commission_date": ("Commission Date", ("Install Date", "Installation Date", "Date Installed", "Purchase Date"), False,
                        "YYYY-MM-DD or D/M/YYYY."),
    "supplier": ("Supplier", ("Vendor", "Supplier Name"), False, "Who it was bought from."),
    "purchase_cost": ("Purchase Cost", ("Cost", "Purchase Price", "Price"), False, "A non-negative number."),
    "warranty_info": ("Warranty Info", ("Warranty", "Warranty Details"), False, "Warranty terms in words."),
    "notes": ("Notes", ("Comments", "Remarks", "Maintenance Notes"), False, "Free text kept on the register."),
}

SCHEDULE_FIELDS: dict[str, tuple[str, ...]] = {
    "pm_id": ("PM ID", ("PM No", "PM Number", "Schedule ID", "PMID"), False,
              "eMaint's reference for this PM; echoed in reports, not stored."),
    "asset_id": ("Asset ID", ("AssetID", "Asset Code", "Equipment ID", "EquipmentID", "Code"), False,
                 "The register machine's Equipment ID. One of this or Asset Description must name a register machine."),
    "asset_name": ("Asset Description", ("Description", "Asset Name", "Name", "Asset", "Equipment Name", "Machine"), False,
                   "The register machine's name. One of this or Asset ID must name a register machine."),
    "title": ("PM Description", ("Task Description", "PM Task", "Description", "Title", "Task", "PM Title"), True,
              "What the job is, e.g. Weekly liner check. Required."),
    "frequency": ("Frequency", ("Interval", "Schedule", "Recurrence", "PM Frequency"), True,
                  "One of: Daily, Weekly, Biweekly, Monthly, Quarterly, Annual. Required."),
    "next_due": ("Next Due", ("Next Due Date", "Due Date", "Next Due On"), False,
                 "First due date, YYYY-MM-DD or D/M/YYYY. Blank means the next occurrence from today."),
    "last_done": ("Last Done", ("Last Completed", "Last Done Date", "Last Performed"), False,
                  "Reference only; echoed in reports, not stored."),
    "allocated_to": ("Assigned To", ("Technician", "Artisan", "Assigned Technician", "Responsible"), False,
                     "Who does the work."),
    "department": ("Department", ("Dept",), False, "Department the jobs go to."),
    "priority": ("Priority", ("Urgency",), False,
                 "One of: Low, Medium, High, Urgent (Critical counts as Urgent). Blank means Medium."),
    "estimated_hours": ("Estimated Hours", ("Est Hours", "Hours", "Est. Hours"), False,
                        "Kept as written, e.g. 2 or 1.5."),
    "instructions": ("Instructions", ("Job Instructions", "Procedure", "Task Instructions", "Method"), False,
                     "How the job is done."),
    "active": ("Active", ("Status", "Is Active", "Enabled"), False,
               "Yes or No (True/False, Active/Inactive work too). Blank means Yes."),
}

# eMaint words -> register status. Anything else is an invalid row, not a
# guess: a silently "Active" scrapped machine would raise PM jobs for scrap.
ASSET_STATUS_MAP = {
    "active": "operational",
    "operational": "operational",
    "in service": "operational",
    "inservice": "operational",
    "running": "operational",
    "under maintenance": "maintenance",
    "maintenance": "maintenance",
    "in maintenance": "maintenance",
    "out of service": "out_of_service",
    "outofservice": "out_of_service",
    "inactive": "out_of_service",
    "retired": "out_of_service",
    "scrapped": "out_of_service",
    "decommissioned": "out_of_service",
    "standby": "reserved",
    "spare": "reserved",
    "reserved": "reserved",
}
ASSET_STATUS_WORDS = "Active, Under Maintenance, Out of Service, Retired, Standby"

CRITICALITY_WORDS = ("High", "Medium", "Low")

# eMaint frequency words -> our recurrence_type. Semi-annual and meter-based
# PMs have no counterpart here and are invalid rows with a reason, not silent
# mis-schedules: a wrong rhythm raises jobs on the wrong dates all year.
FREQUENCY_MAP = {
    "daily": "daily",
    "day": "daily",
    "every day": "daily",
    "weekly": "weekly",
    "week": "weekly",
    "every week": "weekly",
    "biweekly": "biweekly",
    "bi-weekly": "biweekly",
    "fortnightly": "biweekly",
    "every 2 weeks": "biweekly",
    "every two weeks": "biweekly",
    "monthly": "monthly",
    "month": "monthly",
    "every month": "monthly",
    "quarterly": "quarterly",
    "quarter": "quarterly",
    "every 3 months": "quarterly",
    "every quarter": "quarterly",
    "annual": "yearly",
    "annually": "yearly",
    "yearly": "yearly",
    "year": "yearly",
    "every year": "yearly",
}
FREQUENCY_WORDS = "Daily, Weekly, Biweekly, Monthly, Quarterly, Annual"
_METER_WORDS = ("meter", "metre", "hour", "hours", "km", "kilometer", "kilometre",
                "mile", "runtime", "running hours", "usage", "odometer")

PRIORITY_MAP = {
    "low": "low",
    "medium": "medium",
    "med": "medium",
    "normal": "medium",
    "high": "high",
    "urgent": "urgent",
    "critical": "urgent",
    "emergency": "urgent",
}
PRIORITY_WORDS = "Low, Medium, High, Urgent"

_TRUE_WORDS = {"yes", "y", "true", "1", "active", "enabled", "on"}
_FALSE_WORDS = {"no", "n", "false", "0", "inactive", "disabled", "off"}


def _text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "1" if raw else "0"
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw).strip()


def match_headers(columns: list[str], fields: dict) -> tuple[dict[str, Optional[str]], dict[str, float]]:
    """Match spreadsheet headers to field keys by header or alias.

    Returns the mapping (field key -> column name or None) and a confidence
    per field: 1.0 for the template header itself, 0.8 for a known alias, 0
    when nothing matched. First column wins on duplicates."""
    norm_to_col: dict[str, str] = {}
    for col in columns:
        norm_to_col.setdefault(_norm_header(col), col)
    mapping: dict[str, Optional[str]] = {}
    confidence: dict[str, float] = {}
    for key, (header, aliases, _required, _help) in fields.items():
        if _norm_header(header) in norm_to_col:
            mapping[key] = norm_to_col[_norm_header(header)]
            confidence[key] = 1.0
            continue
        found = None
        for alias in aliases:
            if _norm_header(alias) in norm_to_col:
                found = norm_to_col[_norm_header(alias)]
                break
        mapping[key] = found
        confidence[key] = 0.8 if found else 0.0
    return mapping, confidence


def parse_date(raw: Any) -> tuple[Optional[date], Optional[str]]:
    """An import date: ISO (YYYY-MM-DD, datetimes truncated to the day) or
    D/M/YYYY. Anything else is an error naming what was accepted."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    if isinstance(raw, datetime):
        return raw.date(), None
    if isinstance(raw, date):
        return raw, None
    s = str(raw).strip()
    try:  # ISO first: "2026-11-06", also "2026-11-06 00:00:00" from Excel text
        return date.fromisoformat(s[:10]), None
    except ValueError:
        pass
    for sep in ("/", "-", "."):
        parts = s.split(sep)
        if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
            day, month, year = (int(p.strip()) for p in parts)
            if year < 100:
                year += 2000
            try:
                return date(year, month, day), None
            except ValueError:
                break
    return None, f"'{s}' is not a date (use YYYY-MM-DD or D/M/YYYY)"


def parse_cost(raw: Any) -> tuple[Optional[float], Optional[str]]:
    """A non-negative purchase cost. Blank stays blank (None); anything that
    is not a non-negative number is an error, never 0."""
    s = _text(raw).replace(",", "").replace(" ", "")
    for symbol in ("$", "R", "USD", "ZiG"):
        s = s.replace(symbol, "")
    if not s:
        return None, None
    try:
        value = float(s)
    except ValueError:
        return None, f"'{_text(raw)}' is not a cost (a non-negative number)"
    if value < 0:
        return None, f"'{_text(raw)}' is not a cost (a non-negative number)"
    return value, None


def _cells(row: dict[str, Any], mapping: dict[str, Optional[str]]) -> dict[str, Any]:
    """Pick the mapped cell per field key (None when the column is missing)."""
    return {key: (row.get(col) if col else None) for key, col in mapping.items()}


def extract_asset(row: dict[str, Any], mapping: dict[str, Optional[str]]) -> tuple[dict[str, Any], list[str]]:
    """An asset row's register values plus what is wrong with it.

    `values` carries the cleaned fields (dates ISO, cost a float); `held`
    inside it carries Manufacturer/Criticality for the router to store only
    when the register has those columns. `errors` is empty when the row is
    importable."""
    cells = _cells(row, mapping)
    errors: list[str] = []
    values: dict[str, Any] = {
        "asset_id": _text(cells.get("asset_id")),
        "name": _text(cells.get("name")),
        "category": _text(cells.get("category")),
        "model": _text(cells.get("model")),
        "serial_number": _text(cells.get("serial_number")),
        "location": _text(cells.get("location")),
        "department": _text(cells.get("department")),
        "supplier": _text(cells.get("supplier")),
        "warranty_info": _text(cells.get("warranty_info")),
        "notes": _text(cells.get("notes")),
        "held": {},
    }
    if not values["name"]:
        errors.append("No Asset Description (the register name is required)")
    status_word = _text(cells.get("status")) or "Active"
    status = ASSET_STATUS_MAP.get(status_word.lower())
    if status is None:
        errors.append(f"Status '{status_word}' is not one of: {ASSET_STATUS_WORDS}")
        values["status"] = ""
    else:
        values["status"] = status
    crit_word = _text(cells.get("criticality"))
    if crit_word:
        hit = next((w for w in CRITICALITY_WORDS if w.lower() == crit_word.lower()), None)
        if hit is None:
            errors.append(f"Criticality '{crit_word}' is not High, Medium or Low")
        else:
            values["held"]["criticality"] = hit
    maker = _text(cells.get("manufacturer"))
    if maker:
        values["held"]["manufacturer"] = maker
    when, err = parse_date(cells.get("commission_date"))
    if err:
        errors.append(f"Commission Date {err}")
    values["commission_date"] = when.isoformat() if when else ""
    cost, err = parse_cost(cells.get("purchase_cost"))
    if err:
        errors.append(err)
    values["purchase_cost"] = cost
    return values, errors


def extract_schedule(row: dict[str, Any], mapping: dict[str, Optional[str]]) -> tuple[dict[str, Any], list[str]]:
    """A PM row's schedule values plus what is wrong with it.

    `values` carries the cleaned fields and the derived recurrence
    (`recurrence`); `ref` echoes eMaint's PM ID and Last Done for the
    reports (neither is stored). `errors` is empty when importable."""
    cells = _cells(row, mapping)
    errors: list[str] = []
    values: dict[str, Any] = {
        "ref": {"pm_id": _text(cells.get("pm_id")), "asset_id": _text(cells.get("asset_id"))},
        "asset_id": _text(cells.get("asset_id")),
        "asset_name": _text(cells.get("asset_name")),
        "title": _text(cells.get("title")),
        "allocated_to": _text(cells.get("allocated_to")),
        "department": _text(cells.get("department")),
        "estimated_hours": _text(cells.get("estimated_hours")),
        "instructions": _text(cells.get("instructions")),
    }
    if not values["title"]:
        errors.append("No PM Description (the schedule title is required)")
    if not values["asset_id"] and not values["asset_name"]:
        errors.append("No Asset ID or Asset Description (the machine is required)")
    freq_word = _text(cells.get("frequency"))
    recurrence = FREQUENCY_MAP.get(freq_word.lower()) if freq_word else None
    if recurrence is None:
        if freq_word and any(w in freq_word.lower() for w in _METER_WORDS):
            errors.append(
                f"Frequency '{freq_word}' is meter-based (running hours or distance); "
                "meter PMs are not imported — set them up as hour-meter services instead")
        else:
            errors.append(f"Frequency '{freq_word or 'blank'}' is not one of: {FREQUENCY_WORDS}")
        values["frequency"] = ""
    else:
        values["frequency"] = recurrence
    due, err = parse_date(cells.get("next_due"))
    if err:
        errors.append(f"Next Due {err}")
    values["next_due"] = due.isoformat() if due else ""
    last, err = parse_date(cells.get("last_done"))
    if err:
        errors.append(f"Last Done {err}")
    values["ref"]["last_done"] = last.isoformat() if last else ""
    prio_word = _text(cells.get("priority")) or "Medium"
    prio = PRIORITY_MAP.get(prio_word.lower())
    if prio is None:
        errors.append(f"Priority '{prio_word}' is not one of: {PRIORITY_WORDS}")
        values["priority"] = ""
    else:
        values["priority"] = prio
    active_word = _text(cells.get("active")) or "Yes"
    if active_word.lower() in _TRUE_WORDS:
        values["active"] = True
    elif active_word.lower() in _FALSE_WORDS:
        values["active"] = False
    else:
        errors.append(f"Active '{active_word}' is not Yes or No")
        values["active"] = True
    values["recurrence"] = derive_recurrence(values["frequency"], values["next_due"]) if recurrence else {}
    return values, errors


def derive_recurrence(frequency: str, next_due_iso: str) -> dict[str, Any]:
    """Recurrence columns from a mapped frequency and optional first due date.

    Weekly rhythms keep the file's weekday, monthly the day of the month, and
    yearly the month and day, so the imported schedule's dates agree with the
    Next Due the file states. Without a Next Due the rhythms start plain
    (Monday, the 1st, January)."""
    try:
        due = date.fromisoformat(next_due_iso) if next_due_iso else None
    except ValueError:
        due = None
    out: dict[str, Any] = {
        "recurrence_type": frequency,
        # recurrence_dow is isoweekday (1=Monday..7=Sunday), like the stored rows.
        "recurrence_dow": due.isoweekday() if due else 1,
        "recurrence_dom": due.day if due else 1,
        "recurrence_months": [],
    }
    if frequency == "quarterly":
        out["recurrence_months"] = [0, 3, 6, 9]
    elif frequency == "yearly":
        out["recurrence_months"] = [(due.month - 1) if due else 0]
    return out


def _norm_name(name: Any) -> str:
    return str(name or "").strip().lower()


def match_asset(values: dict[str, Any], register: list[dict[str, Any]]) -> tuple[str, Optional[dict], str]:
    """Match cleaned asset values to the register.

    "exists" (exact Equipment ID or exact trimmed name — the row will be
    skipped, never overwritten); "close" (case-insensitive name hit — held
    for a human, never auto-matched, because a write must not guess); else
    "new". The note explains the decision for the report."""
    code = values.get("asset_id") or ""
    name = values.get("name") or ""
    by_code = {str(r.get("equipment_id") or ""): r for r in register if str(r.get("equipment_id") or "")}
    by_name = {str(r.get("name") or "").strip(): r for r in register}
    if code and code in by_code:
        hit = by_code[code]
        return "exists", hit, f"Equipment ID {code} is already on the register ({hit.get('name')})"
    if name and name in by_name:
        hit = by_name[name]
        other = str(hit.get("equipment_id") or "")
        drift = f" (register code {other} differs from the file's {code})" if code and other and code != other else ""
        return "exists", hit, f"'{name}' is already on the register{drift}"
    norm = _norm_name(name)
    if norm:
        for row in register:
            if _norm_name(row.get("name")) == norm:
                return ("close", row,
                        f"Not imported: '{name}' closely matches '{row.get('name')}' on the register "
                        f"({row.get('equipment_id') or 'no code'}). Fix the spelling in the file or the register first.")
    return "new", None, ""


def find_machine(asset_id: str, asset_name: str, register: list[dict[str, Any]]) -> tuple[Optional[dict], Optional[dict]]:
    """The register machine a PM row names: exact Equipment ID first, then
    exact trimmed name (a write must not guess). Returns (match, close_hint);
    the hint is a case-insensitive name hit for the report, never a match."""
    if asset_id:
        for row in register:
            if str(row.get("equipment_id") or "") == asset_id:
                return row, None
    name = (asset_name or "").strip()
    if name:
        for row in register:
            if str(row.get("name") or "").strip() == name:
                return row, None
    norm = _norm_name(asset_name)
    if norm:
        for row in register:
            if _norm_name(row.get("name")) == norm:
                return None, row
    return None, None


def flag_asset_duplicates(previews: list[dict[str, Any]]) -> None:
    """Mark second-and-later rows that repeat an Asset ID or exact name from
    the same file as invalid, in place. The first occurrence keeps its
    status; importing both would double the machine."""
    seen_code: dict[str, int] = {}
    seen_name: dict[str, int] = {}
    for preview in previews:
        if preview.get("errors"):
            continue
        values = preview.get("values", {})
        code, name = values.get("asset_id") or "", values.get("name") or ""
        if code:
            if code in seen_code:
                preview["status"] = "invalid"
                preview["errors"] = [f"Same Asset ID as row {seen_code[code]} in this file (imported once)"]
                continue
            seen_code[code] = preview["row"]
        if name:
            if name in seen_name:
                preview["status"] = "invalid"
                preview["errors"] = [f"Same name as row {seen_name[name]} in this file (imported once)"]
                continue
            seen_name[name] = preview["row"]


def flag_schedule_duplicates(previews: list[dict[str, Any]]) -> None:
    """Mark second-and-later rows for the same machine and title (case- and
    space-insensitive) as invalid, in place."""
    seen: dict[tuple[str, str], int] = {}
    for preview in previews:
        if preview.get("errors"):
            continue
        values = preview.get("values", {})
        key = (_norm_name(values.get("asset_id") or values.get("asset_name")),
               _norm_name(values.get("title")))
        if key in seen:
            preview["status"] = "invalid"
            preview["errors"] = [f"Same machine and title as row {seen[key]} in this file (imported once)"]
            continue
        seen[key] = preview["row"]


def build_asset_insert(values: dict[str, Any], extra_columns: set[str]) -> dict[str, Any]:
    """The equipment insert for cleaned values: only columns the register is
    known to hold, plus Manufacturer/Criticality when `extra_columns` (probed
    with has_columns) says they exist. Blank fields are left out so register
    defaults apply."""
    insert: dict[str, Any] = {}
    if values.get("asset_id"):
        insert["equipment_id"] = values["asset_id"]
    insert["name"] = values["name"]
    insert["status"] = values.get("status") or "operational"
    for key in ("category", "model", "serial_number", "location", "department",
                "supplier", "warranty_info"):
        if values.get(key):
            insert[key] = values[key]
    if values.get("notes"):
        insert["maintenance_notes"] = values["notes"]
    if values.get("commission_date"):
        insert["commission_date"] = values["commission_date"]
    if values.get("purchase_cost") is not None:
        insert["purchase_cost"] = values["purchase_cost"]
    for key in ("manufacturer", "criticality"):
        if key in extra_columns and (values.get("held") or {}).get(key):
            insert[key] = values["held"][key]
    return insert


def build_schedule_insert(values: dict[str, Any], machine: dict[str, Any], links_active: bool) -> dict[str, Any]:
    """The maintenance_schedules insert for cleaned PM values and its matched
    register machine. equipment_info always names the register machine (exact
    register spelling); the M1 link columns ride along only once that SQL
    has run."""
    rec = values.get("recurrence") or {}
    insert: dict[str, Any] = {
        "name": values["title"],
        "equipment_info": str(machine.get("name") or ""),
        "to_department": values.get("department") or "",
        "allocated_to": values.get("allocated_to") or "",
        "authorising_foreman": "",
        "estimated_hours": values.get("estimated_hours") or "",
        "job_request_details": values.get("title") or "",
        "job_instructions": values.get("instructions") or "",
        "priority": values.get("priority") or "medium",
        "recurrence_type": rec.get("recurrence_type") or "weekly",
        "recurrence_dow": rec.get("recurrence_dow") or 1,
        "recurrence_dom": rec.get("recurrence_dom") or 1,
        "recurrence_months": rec.get("recurrence_months") or [],
        "specific_dates": [],
        "advance_days": 0,
        "active": values.get("active", True),
    }
    if values.get("next_due"):
        insert["next_due_date"] = values["next_due"]
    if links_active:
        insert["equipment_id"] = machine.get("id")
        insert["classification"] = "planned_maintenance"
    return insert


def template_headers(fields: dict) -> list[str]:
    """The downloadable template's header row, in field order."""
    return [header for header, _aliases, _required, _help in fields.values()]
