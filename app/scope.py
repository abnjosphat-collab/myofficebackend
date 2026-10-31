# backend/app/scope.py — one registry: for each scoped table, how a row belongs to
# a section, and the helpers routers use to enforce it. The server enforces the
# limit; the browser only makes it obvious. Rules:
#
# - "section": the row carries the section itself (row[column] must equal the
#   viewer's scope; NULL/"" never matches). Covers existing section columns
#   and the Batch 4 columns alike — the SQL lands before the code that reads it.
# - "person": the row links a person (column holds the register id as text, or
#   the db id when id_kind is "db"); visible iff that person is in scope.
# - "members": the row carries a small member list (rotations, covers); visible
#   iff ANY member is in scope. Rotations are handfuls of rows, so lists are
#   filtered in Python, not in the query.
# - "inherits": the row belongs to a parent row (via column); visible iff the
#   parent is visible. Unlinked rows (NULL parent, e.g. a reading not tied to a
#   machine) are hidden from scoped viewers, same as the NULL rule.
# - "subtree": documents — Batch 5 (visible iff under the scope root folder).
#
# Scopes are structured: {"kind": "section"|"department", "value": str}.
# Legacy string scopes (and the Projects accounts) mean section. A department
# scope (Mining) resolves people by employees.department instead of section,
# and every section-rule table fails closed for it: none shown, never all.
# Notices additionally name a dept_column, so a department viewer still sees
# company-wide (General/blank) and own-department notices.
#
# Out-of-scope single records answer 404. Unscoped viewers skip every helper
# untouched (responses byte-identical to today). Unknown table + scoped viewer
# fails CLOSED (loud 500, never a silent leak) so a forgotten table cannot
# slip through; the Batch 0 guard test also fails any read that bypasses this
# module entirely.
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from fastapi import HTTPException

TABLES: dict[str, dict[str, Any]] = {
    # -- direct section ----------------------------------------------------
    "employees": {"rule": "section", "column": "section", "dept_column": "department"},
    "nearmiss_reports": {"rule": "section", "column": "section"},
    "sheq_inspections": {"rule": "section", "column": "section"},
    "work_stoppage_reports": {"rule": "section", "column": "section"},
    "pto_reports": {"rule": "section", "column": "section"},
    "requisitions": {"rule": "section", "column": "section"},
    "shift_handovers": {"rule": "section", "column": "section"},
    "job_cards": {"rule": "section", "column": "section"},
    "pachedu_reports": {"rule": "section", "column": "section_choice"},
    "vfl_reports": {"rule": "section", "column": "section_choice"},
    "equipment": {"rule": "section", "column": "section"},
    "work_orders": {"rule": "section", "column": "section"},
    "breakdowns": {"rule": "section", "column": "section"},
    "notices": {"rule": "section", "column": "section", "dept_column": "department", "dept_open": ["General"]},
    "sop_documents": {"rule": "section", "column": "section"},
    "services": {"rule": "section", "column": "section"},
    "daily_reports": {"rule": "section", "column": "section"},
    "maintenance_schedules": {"rule": "section", "column": "section"},
    # Whole-mine complaints stay visible to every section: hiding a legacy or
    # "General" complaint could hide a hazard in the viewer's own workplace.
    "safety_complaints": {"rule": "section", "column": "section",
                          "open_values": ["General", None, ""]},
    # -- through person ----------------------------------------------------
    "leaves": {"rule": "person", "column": "employee_id", "id_kind": "register"},
    "overtime": {"rule": "person", "column": "employee_id", "id_kind": "register"},
    "timesheets": {"rule": "person", "column": "employee_id", "id_kind": "db"},
    "artisan_timesheets": {"rule": "person", "column": "employee_db_id", "id_kind": "db",
                              "column2": "employee_id", "id_kind2": "register"},
    "ppe_records": {"rule": "person", "column": "employee_id", "id_kind": "register"},
    "ppe_order_list": {"rule": "person", "column": "employee_id", "id_kind": "register", "json": "entry"},
    "training_certifications": {"rule": "person", "column": "employee_id", "id_kind": "register"},
    "competency_matrix": {"rule": "person", "column": "employee_id", "id_kind": "register"},
    "standby_schedules": {"rule": "person", "column": "employee_id", "id_kind": "register"},
    "duty_roster": {"rule": "person", "column": "employee_id", "id_kind": "register"},
    # Recipient link is optional; a recipient_name fallback match is resolved
    # by the router (Batch 2) before calling the person helpers with the id.
    "stock_issues": {"rule": "person", "column": "recipient_id", "id_kind": "register"},
    # -- member lists (filtered in Python; tiny tables) --------------------
    "standby_rotations": {"rule": "members", "members": "members", "column": "section"},
    "duty_rotations": {"rule": "members", "members": "members"},
    "rotation_covers": {"rule": "members", "members": ("absent_employee_id", "cover_employee_id")},
    # -- inherits a parent -------------------------------------------------
    "sheq_findings": {"rule": "inherits", "parent": "sheq_inspections", "via": "inspection_id"},
    "requisition_items": {"rule": "inherits", "parent": "requisitions", "via": "requisition_id"},
    "pto_action_plan": {"rule": "inherits", "parent": "pto_reports", "via": "report_id"},
    "vfl_action_plan": {"rule": "inherits", "parent": "vfl_reports", "via": "report_id"},
    "sop_revisions": {"rule": "inherits", "parent": "sop_documents", "via": "sop_id"},
    "service_attachments": {"rule": "inherits", "parent": "services", "via": "service_id"},
    "corrective_actions": {"rule": "inherits", "parent": "work_stoppage_reports", "via": "report_id"},
    "availabilities": {"rule": "inherits", "parent": "equipment", "via": "equipment_id"},
    # The table is compliance_register (Batch 0/1 shortened it to "compliance",
    # which also left the router invisible to the guard test).
    "compliance_register": {"rule": "inherits", "parent": "equipment", "via": "equipment_id"},
    "condition_monitoring": {"rule": "inherits", "parent": "equipment", "via": "equipment_id"},
    # lube_schedules carries its own section column (the list filter and the
    # create payload both use it), so it is direct-section: the Batch 1
    # inherits entry would have hidden every row until Batch 4 wires
    # equipment. lube_records (history) inherit their schedule instead.
    "lube_schedules": {"rule": "section", "column": "section"},
    "lube_records": {"rule": "inherits", "parent": "lube_schedules", "via": "schedule_id"},
    # -- documents subtree (Batch 5) ---------------------------------------
    # Visible iff at or under the scope's root folder: the top-level folder
    # named for the scope. Unfiled rows (NULL link) are hidden from scoped
    # viewers, same as the NULL rule. Activities resolve through their
    # document and are never listed standalone (the router guards the doc).
    "documents": {"rule": "subtree", "via": "folder_ref"},
    "document_folders": {"rule": "subtree", "via": "id"},
    "document_activities": {"rule": "subtree", "via": "document_id",
                            "parent": "documents", "parent_via": "folder_ref"},
}


#: Departments kept apart by default: unscoped lists leave their people out
#: unless the request explicitly asks for them (admins/managers only).
SEPARATE_DEPARTMENTS = ["Mining"]


#: The one stored shape for a department limit (Admin writes this;
#: sections stay plain strings, as the Projects accounts have them).
#: Reads also tolerate {"kind", "value"} so nothing depends on which of the
#: two spellings a row carries.
DEPARTMENT_SCOPE_KEY = "department"


def _non_empty_str(value: Any) -> Optional[str]:
    """A stripped string, or None for blank/non-strings."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _coerce_scope(raw: Any) -> Optional[dict]:
    """A raw scope value (legacy section string, {"department"/"section"} name,
    or {"kind", "value"}) to the structured {"kind": "section"|"department",
    "value": str}, or None. Anything else is not a limit: None."""
    if isinstance(raw, dict):
        kind = raw.get("kind")
        value = _non_empty_str(raw.get("value"))
        if kind in ("section", "department") and value is not None:
            return {"kind": kind, "value": value}
        if set(raw) == {DEPARTMENT_SCOPE_KEY}:
            dep = _non_empty_str(raw[DEPARTMENT_SCOPE_KEY])
            return {"kind": "department", "value": dep} if dep is not None else None
        if set(raw) == {"section"}:
            sec = _non_empty_str(raw["section"])
            return {"kind": "section", "value": sec} if sec is not None else None
        return None
    text = _non_empty_str(raw)
    if text is not None:
        return {"kind": "section", "value": text}
    return None


def viewer_scope(user: Optional[dict]) -> Optional[dict]:
    """The limit on a signed-in viewer, or None for a full view. Structured:
    {"kind": "section"|"department", "value": str}. Reads the top-level
    `scope` get_current_user sets; falls back to `permissions.scope` for raw
    profile rows. Tolerates sessions minted before scoping existed (no key →
    unscoped), and legacy string scopes (always sections).
    """
    if not isinstance(user, dict):
        return None
    scope = user.get("scope")
    if scope is None or scope == "":
        perms = user.get("permissions") or {}
        scope = perms.get("scope") if isinstance(perms, dict) else None
    return _coerce_scope(scope)


def scope_section(scope: Optional[dict]) -> Optional[str]:
    """The section of a structured scope dict, or None (unscoped, or
    department-limited). Routers holding viewer_scope() output use this;
    section_of() below is the same read from the user dict."""
    if isinstance(scope, dict) and scope.get("kind") == "section":
        value = scope.get("value")
        return value if isinstance(value, str) else None
    return None


def section_of(user: Optional[dict]) -> Optional[str]:
    """The viewer's section string, or None (unscoped, or department-limited)."""
    scope = viewer_scope(user)
    return scope_section(viewer_scope(user))
    return None


def scope_value(scope: Optional[dict]) -> Optional[str]:
    """The value of a structured scope (either kind), or None."""
    return scope["value"] if isinstance(scope, dict) else None


def scope_cache_key(scope: Optional[dict]) -> Optional[str]:
    """A cache-key segment that keeps section and department slices apart."""
    if scope is None:
        return None
    return f"{scope['kind']}:{scope['value']}"


def cache_scope_segment(raw: Any) -> Optional[str]:
    """scope_cache_key() for the raw `scope` on a user dict (string or dict)."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        coerced = _coerce_scope(raw)
        return scope_cache_key(coerced)
    return str(raw) if isinstance(raw, str) else None


def scope_person_ids(db: Any, scope: str) -> dict[str, set]:
    """Everyone in section `scope`: register ids (text), db ids (ints) and
    normalized full names (for the stock-issues name fallback). One query;
    routers call it once per request and pass the result down."""
    resp = db.table("employees").select("id,employee_id,first_name,last_name").eq("section", scope).execute()
    register, db_ids, names = set(), set(), set()
    for row in resp.data or []:
        if row.get("employee_id"):
            register.add(str(row["employee_id"]))
        if row.get("id") is not None:
            db_ids.add(row["id"])
        full = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
        if full:
            names.add(" ".join(full.split()).lower())
    return {"register": register, "db": db_ids, "names": names}


def scope_person_ids_for(db: Any, scope: dict) -> dict[str, set]:
    """scope_person_ids() for either scope kind: sections match the section
    column, departments match employees.department. Blank departments never
    match a department scope (they stay Engineering)."""
    if scope["kind"] == "department":
        resp = db.table("employees").select("id,employee_id,first_name,last_name").eq("department", scope["value"]).execute()
    else:
        resp = db.table("employees").select("id,employee_id,first_name,last_name").eq("section", scope["value"]).execute()
    register, db_ids, names = set(), set(), set()
    for row in resp.data or []:
        if row.get("employee_id"):
            register.add(str(row["employee_id"]))
        if row.get("id") is not None:
            db_ids.add(row["id"])
        full = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
        if full:
            names.add(" ".join(full.split()).lower())
    return {"register": register, "db": db_ids, "names": names}


def person_ids_for(db: Any, user: Optional[dict]) -> Optional[dict]:
    """scope_person_ids_for() for a scoped viewer, else None (every helper
    short-circuits on None). Routers call this once per request."""
    scope = viewer_scope(user)
    return scope_person_ids_for(db, scope) if scope is not None else None


def _scope_string(scope: Any) -> Optional[str]:
    """A structured (or legacy string) scope to its section string, or None
    for departments and unscoped viewers. Documents subtrees are section
    things; department viewers see none."""
    if isinstance(scope, dict):
        return scope["value"] if scope.get("kind") == "section" else None
    return scope if isinstance(scope, str) and scope else None


def scope_root_id(folders: list, scope: Any) -> Optional[str]:
    """The scope's root folder: the top-level folder named for the scope.

    Case-insensitive (name_taken() already forbids two top-level folders
    that differ only by case, so the match is unambiguous); exact match
    wins for determinism. None when nobody has made the root yet — the
    scoped library is then honestly empty.
    """
    section = _scope_string(scope)
    if section is None:
        return None
    tops = [f for f in folders if not f.get("parent_id")]
    for folder in tops:
        if (folder.get("name") or "") == section:
            return str(folder["id"])
    lowered = section.lower()
    for folder in tops:
        if str(folder.get("name") or "").lower() == lowered:
            return str(folder["id"])
    return None


def scope_subtree_ids(folders: list, scope: Any) -> list:
    """Root + descendants as strings, [] when there is no root yet. Pure:
    routers that already loaded the folders pass them in."""
    from app.document_rules import descendant_ids

    root = scope_root_id(folders, scope)
    return descendant_ids(folders, root) if root else []


def load_scope_subtree_ids(db: Any, scope: Any) -> list:
    """scope_subtree_ids() with the folders fetched (minimal columns)."""
    resp = db.table("document_folders").select("id,parent_id,name").execute()
    return scope_subtree_ids(resp.data or [], scope)


def _rule(table: str, scope: dict) -> dict:
    """The registry entry for `table`. Callers return early for unscoped
    viewers (scope None) before reaching here, so an unknown table always
    means a scoped viewer hitting a forgotten table: fail closed."""
    try:
        return TABLES[table]
    except KeyError:
        raise RuntimeError(f"Table {table!r} has no scope rule — refusing to serve it to a scoped viewer")


def _person_ref_match(column: str, db_kind: bool, row: dict, bank: dict) -> bool:
    ids = bank["db" if db_kind else "register"]
    ref = row.get(column)
    ref = str(ref) if not db_kind and ref is not None else ref
    return ref in ids


def _person_row(rule: dict, row: dict) -> dict:
    """The dict carrying the person link — the row itself, or its nested JSON
    object for tables that keep the link inside a payload column."""
    if "json" not in rule:
        return row
    nested = row.get(rule["json"])
    return nested if isinstance(nested, dict) else {}


def _person_match(rule: dict, row: dict, bank: dict) -> bool:
    """Either link in scope (a table with one link column just checks that)."""
    if _person_ref_match(rule["column"], rule.get("id_kind") == "db", row, bank):
        return True
    return "column2" in rule and _person_ref_match(
        rule["column2"], rule.get("id_kind2") == "db", row, bank)


def _in_or_empty_2(query: Any, rule: dict, bank: dict) -> Any:
    """Dual-link person predicate: one or_() with two in-lists. An empty side
    is dropped (it could match nothing anyway); both empty spells
    match-nothing."""
    col1, db1 = rule["column"], rule.get("id_kind") == "db"
    col2, db2 = rule["column2"], rule.get("id_kind2") == "db"
    o1 = sorted(bank["db" if db1 else "register"], key=str)
    o2 = sorted(bank["db" if db2 else "register"], key=str)
    if o1 and o2:
        q1 = ",".join(str(v) if db1 else json.dumps(str(v)) for v in o1)
        q2 = ",".join(str(v) if db2 else json.dumps(str(v)) for v in o2)
        return query.or_(f"{col1}.in.({q1}),{col2}.in.({q2})")
    if o1:
        return query.in_(col1, o1)
    if o2:
        return query.in_(col2, o2)
    return query.eq(col1, -1 if db1 else "__scope_empty__")


def _in_or_empty(query: Any, column: str, values: Any, int_column: bool) -> Any:
    """IN-predicate that stays valid when the set is empty: PostgREST rejects
    an empty in.() list, so spell "match nothing" as an impossible equality
    instead (ids are positive SERIALs / mine numbers, never these)."""
    ordered = sorted(values, key=str)
    if ordered:
        return query.in_(column, ordered)
    return query.eq(column, -1 if int_column else "__scope_empty__")


def apply_list_scope(query: Any, user: Optional[dict], table: str, db: Any = None,
                     person_ids: Optional[dict] = None) -> Any:
    """Add the scope predicate to a list query. Unscoped viewers pass through
    untouched. "members" tables cannot be predicated in the query — the router
    filters those rows in Python with filter_rows()."""
    scope = viewer_scope(user)
    if scope is None:
        return query
    rule = _rule(table, scope)
    kind = rule["rule"]
    dept = scope["kind"] == "department"
    if kind == "section":
        if dept:
            # A department viewer sees through the department column where the
            # table names one (notices: own department + company-wide
            # "General"; employees: exactly their own department). Every other
            # section-rule table shows none, never all.
            if "dept_column" in rule:
                col = rule["dept_column"]
                extra = rule.get("dept_open") or []
                if extra:
                    terms = ",".join([f"{col}.eq.{scope['value']}"] + [f"{col}.eq.{v}" for v in extra])
                    return query.or_(terms)
                return query.eq(col, scope["value"])
            return query.eq(rule["column"], "__scope_empty__")
        return query.eq(rule["column"], scope["value"])
    if kind == "person":
        if "json" in rule:
            raise RuntimeError(f"Table {table!r} keeps its person link in JSON — filters in Python (filter_rows), not in the query")
        bank = person_ids or scope_person_ids_for(db, scope)
        if "column2" not in rule:
            ids = bank["db" if rule.get("id_kind") == "db" else "register"]
            return _in_or_empty(query, rule["column"], ids, rule.get("id_kind") == "db")
        return _in_or_empty_2(query, rule, bank)
    if kind == "inherits":
        if dept:
            return _in_or_empty(query, rule["via"], [], True)
        parent = rule["parent"]
        parent_rule = TABLES[parent]
        assert parent_rule["rule"] == "section", f"inherits parent {parent} is not section-ruled"
        resp = db.table(parent).select("id").eq(parent_rule["column"], scope["value"]).execute()
        return _in_or_empty(query, rule["via"], [r["id"] for r in (resp.data or [])], True)
    if kind == "members":
        raise RuntimeError(f"Table {table!r} filters in Python (filter_rows), not in the query")
    if kind == "subtree":
        if dept:
            return _in_or_empty(query, rule["via"], [], False)
        if "parent" in rule:
            raise RuntimeError(f"Table {table!r} is visible through its parent — guard the document, never list it standalone")
        return _in_or_empty(query, rule["via"], load_scope_subtree_ids(db, scope), False)
    raise RuntimeError(f"Table {table!r} uses rule {kind!r}, which has no list predicate")


def row_visible(user: Optional[dict], table: str, row: dict, db: Any = None,
                person_ids: Optional[dict] = None) -> bool:
    """True iff `row` may be shown to `user`. Pure except for the person-id
    lookup, which routers pass in."""
    scope = viewer_scope(user)
    if scope is None:
        return True
    rule = _rule(table, scope)
    kind = rule["rule"]
    dept = scope["kind"] == "department"
    if kind == "section":
        if dept:
            if "dept_column" in rule:
                return row.get(rule["dept_column"]) in [scope["value"]] + list(rule.get("dept_open") or [])
            return False
        value = row.get(rule["column"])
        if value == scope["value"]:
            return True
        # Only tables that name open_values (whole-mine complaints) ever show
        # a blank or shared row; everywhere else NULL/"" never matches.
        return value in (rule.get("open_values") or [])
    if kind == "person":
        return _person_match(rule, _person_row(rule, row), person_ids or scope_person_ids_for(db, scope))
    if kind == "members":
        ids = (person_ids or scope_person_ids_for(db, scope))["register"]
        spec = rule["members"]
        if isinstance(spec, str) and rule.get("column") and not dept and row.get(rule["column"]) == scope["value"]:
            return True
        people: list = []
        if isinstance(spec, str):
            members = row.get(spec) or []
            for m in members:
                if not isinstance(m, dict):
                    continue
                people.append(m.get("employee_id"))
                for c in m.get("crew") or []:
                    if isinstance(c, dict):
                        people.append(c.get("employee_id"))
        else:
            people = [row.get(col) for col in spec]
        return any(p is not None and str(p) in ids for p in people)
    if kind == "inherits":
        if dept:
            return False
        parent = rule["parent"]
        parent_rule = TABLES[parent]
        if row.get(rule["via"]) is None:
            return False
        resp = db.table(parent).select("id," + parent_rule["column"]).eq(
            "id", row[rule["via"]]).execute()
        rows = resp.data or []
        if not rows:
            return False
        return row_visible(user, parent, rows[0], db, person_ids)
    if kind == "subtree":
        if dept:
            return False
        link = row.get(rule["via"])
        if "parent" in rule:
            if link is None:
                return False
            resp = db.table(rule["parent"]).select("id," + rule["parent_via"]).eq("id", link).execute()
            grows = resp.data or []
            if not grows:
                return False
            link = grows[0].get(rule["parent_via"])
        if link is None:
            return False
        return str(link) in load_scope_subtree_ids(db, scope)
    raise RuntimeError(f"Table {table!r} uses rule {kind!r}, which has no row check yet")


def filter_rows(user: Optional[dict], table: str, rows: list, db: Any = None,
                person_ids: Optional[dict] = None) -> list:
    """Python-side filter for "members" tables (and anything else already
    loaded). Unscoped viewers get the rows back untouched."""
    if viewer_scope(user) is None:
        return rows
    return [r for r in rows if row_visible(user, table, r, db, person_ids)]


def assert_row_visible(user: Optional[dict], table: str, row: Optional[dict], db: Any = None,
                       person_ids: Optional[dict] = None, detail: str = "Not found") -> dict:
    """Return the row iff visible, else 404 — missing and out-of-scope are
    indistinguishable by design."""
    if row is None or not row_visible(user, table, row, db, person_ids):
        raise HTTPException(status_code=404, detail=detail)
    return row


def stamp_create(user: Optional[dict], table: str, payload: dict, db: Any = None,
                 person_ids: Optional[dict] = None) -> dict:
    """Force a create payload into scope: section columns are stamped (never
    trusted from the browser); person links to out-of-scope people refuse
    with 404. Unscoped callers pass through unchanged."""
    scope = viewer_scope(user)
    if scope is None:
        return payload
    rule = _rule(table, scope)
    out = dict(payload)
    kind = rule["rule"]
    if kind == "section":
        if scope["kind"] == "department":
            # People are department-stamped (the register owns the department);
            # every other section-rule table refuses department creates.
            if table == "employees":
                out["department"] = scope["value"]
                return out
            raise HTTPException(status_code=404, detail="Not found")
        out[rule["column"]] = scope["value"]
        return out
    if kind == "person":
        if _person_match(rule, _person_row(rule, out), person_ids or scope_person_ids_for(db, scope)):
            return out
        raise HTTPException(status_code=404, detail="Not found")
    if kind == "inherits":
        parent = rule["parent"]
        parent_rule = TABLES[parent]
        if parent_rule["rule"] != "section":
            raise RuntimeError(f"Cannot stamp {table!r}: parent {parent} is not section-ruled")
        if scope["kind"] == "department":
            raise HTTPException(status_code=404, detail="Not found")
        resp = db.table(parent).select("id," + parent_rule["column"]).eq(
            "id", out.get(rule["via"])).execute()
        rows = resp.data or []
        if not rows or rows[0].get(parent_rule["column"]) != scope["value"]:
            raise HTTPException(status_code=404, detail="Not found")
        return out
    if kind == "members":
        # Symmetric with reads: the creator must be able to SEE the row they
        # are creating (their section on it, or any member in scope).
        if not row_visible(user, table, out, db, person_ids):
            raise HTTPException(status_code=404, detail="Not found")
        return out
    raise RuntimeError(f"Table {table!r} uses rule {kind!r}, which cannot stamp creates")
def person_in_scope(person: dict, scope: dict) -> bool:
    """True iff a register row belongs to the scope: sections match section,
    departments match department (blank departments never match — they stay
    Engineering)."""
    if scope["kind"] == "section":
        return (person.get("section") or "") == scope["value"]
    return (person.get("department") or "") == scope["value"]


def requested_department(user: Optional[dict], requested: Optional[str]) -> Optional[str]:
    """Validate a ?department= ask for a separate department (Mining): returns
    the canonical name, or None when nothing (separate) was asked. Only admins
    and managers may ask; everyone else gets 403. Any other department value
    is not a separate department and passes through as None — the caller's
    normal filter handles it."""
    if not requested or not str(requested).strip():
        return None
    want = str(requested).strip()
    for separate in SEPARATE_DEPARTMENTS:
        if want.lower() == separate.lower():
            from app.auth import role_at_least
            if not role_at_least((user or {}).get("role", "viewer"), "manager"):
                raise HTTPException(status_code=403, detail=f"Only managers can view the {separate} department.")
            return separate
    return None


def exclude_separate_departments(query: Any, column: str = "department") -> Any:
    """Leave separate-department rows out of an unscoped list query. Blank
    and NULL departments are kept (they stay Engineering) — plain neq would
    drop NULLs, so the predicate is an explicit OR."""
    values = ",".join(SEPARATE_DEPARTMENTS)
    return query.or_(f"{column}.not.in.({values}),{column}.is.null")


def separate_register_ids(db: Any) -> set:
    """Register ids of everyone in a separate department (for JSON-linked
    tables like the order list that cannot predicate in the query)."""
    ids: set = set()
    for separate in SEPARATE_DEPARTMENTS:
        ids |= scope_person_ids_for(db, {"kind": "department", "value": separate})["register"]
    return ids


def unscoped_separate_filter(db: Any, user: Optional[dict], requested: Optional[str],
                             rows: list, person_of: Callable[[dict], Any]) -> list:
    """Hide separate-department people's rows from unscoped viewers — for
    lists whose person link has no department column to predicate on (the PPE
    order list keeps its person inside a JSON entry). Scoped viewers pass
    through: their slice is already enforced by the caller. Unscoped viewers
    lose separate-department rows unless they asked ?department=<separate>
    (managers/admins only, 403 otherwise), in which case they see exactly
    that slice. A link that matches nobody (blank/unknown id) stays
    Engineering: kept by default, dropped from a separate slice."""
    if viewer_scope(user) is not None:
        return rows
    asked = requested_department(user, requested)
    if asked:
        bank = scope_person_ids_for(db, {"kind": "department", "value": asked})["register"]
        return [r for r in rows if str(person_of(r) or "") in bank]
    excluded = separate_register_ids(db)
    return [r for r in rows if str(person_of(r) or "") not in excluded]
