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
# Out-of-scope single records answer 404. Unscoped viewers skip every helper
# untouched (responses byte-identical to today). Unknown table + scoped viewer
# fails CLOSED (loud 500, never a silent leak) so a forgotten table cannot
# slip through; the Batch 0 guard test also fails any read that bypasses this
# module entirely.
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

TABLES: dict[str, dict[str, Any]] = {
    # -- direct section ----------------------------------------------------
    "employees": {"rule": "section", "column": "section"},
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
    "notices": {"rule": "section", "column": "section"},
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
    "artisan_timesheets": {"rule": "person", "column": "employee_db_id", "id_kind": "db"},
    "ppe_records": {"rule": "person", "column": "employee_id", "id_kind": "register"},
    "ppe_order_list": {"rule": "person", "column": "employee_id", "id_kind": "register"},
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
    "compliance": {"rule": "inherits", "parent": "equipment", "via": "equipment_id"},
    "condition_monitoring": {"rule": "inherits", "parent": "equipment", "via": "equipment_id"},
    "lube_schedules": {"rule": "inherits", "parent": "equipment", "via": "equipment_id"},
    # -- documents subtree (Batch 5) ---------------------------------------
    "documents": {"rule": "subtree"},
    "document_folders": {"rule": "subtree"},
    "document_activities": {"rule": "subtree"},
}


def viewer_scope(user: Optional[dict]) -> Optional[str]:
    """The section a signed-in viewer is limited to, or None for a full view.
    Tolerates sessions minted before permissions existed (no key → unscoped).
    """
    if not isinstance(user, dict):
        return None
    perms = user.get("permissions") or {}
    scope = perms.get("scope") if isinstance(perms, dict) else None
    return scope if scope else None


def scope_person_ids(db: Any, scope: str) -> dict[str, set]:
    """Everyone in `scope`: register ids (text) and db ids (ints). One query;
    routers call it once per request and pass the result down."""
    resp = db.table("employees").select("id,employee_id").eq("section", scope).execute()
    register, db_ids = set(), set()
    for row in resp.data or []:
        if row.get("employee_id"):
            register.add(str(row["employee_id"]))
        if row.get("id") is not None:
            db_ids.add(row["id"])
    return {"register": register, "db": db_ids}


def _rule(table: str, scope: str) -> dict:
    """The registry entry for `table`. Callers return early for unscoped
    viewers (scope None) before reaching here, so an unknown table always
    means a scoped viewer hitting a forgotten table: fail closed."""
    try:
        return TABLES[table]
    except KeyError:
        raise RuntimeError(f"Table {table!r} has no scope rule — refusing to serve it to a scoped viewer")


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
    if kind == "section":
        return query.eq(rule["column"], scope)
    if kind == "person":
        ids = (person_ids or scope_person_ids(db, scope))[
            "db" if rule.get("id_kind") == "db" else "register"]
        return query.in_(rule["column"], sorted(ids, key=str))
    if kind == "inherits":
        parent = rule["parent"]
        parent_rule = TABLES[parent]
        assert parent_rule["rule"] == "section", f"inherits parent {parent} is not section-ruled"
        resp = db.table(parent).select("id").eq(parent_rule["column"], scope).execute()
        return query.in_(rule["via"], [r["id"] for r in (resp.data or [])])
    if kind == "members":
        raise RuntimeError(f"Table {table!r} filters in Python (filter_rows), not in the query")
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
    if kind == "section":
        value = row.get(rule["column"])
        if value == scope:
            return True
        # Only tables that name open_values (whole-mine complaints) ever show
        # a blank or shared row; everywhere else NULL/"" never matches.
        return value in (rule.get("open_values") or [])
    if kind == "person":
        ids = (person_ids or scope_person_ids(db, scope))[
            "db" if rule.get("id_kind") == "db" else "register"]
        ref = row.get(rule["column"])
        ref = str(ref) if rule.get("id_kind") != "db" and ref is not None else ref
        return ref in ids
    if kind == "members":
        ids = (person_ids or scope_person_ids(db, scope))["register"]
        spec = rule["members"]
        if isinstance(spec, str) and rule.get("column") and row.get(rule["column"]) == scope:
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
        out[rule["column"]] = scope
        return out
    if kind == "person":
        ids = (person_ids or scope_person_ids(db, scope))[
            "db" if rule.get("id_kind") == "db" else "register"]
        ref = out.get(rule["column"])
        ref = str(ref) if rule.get("id_kind") != "db" and ref is not None else ref
        if ref not in ids:
            raise HTTPException(status_code=404, detail="Not found")
        return out
    if kind == "inherits":
        parent = rule["parent"]
        parent_rule = TABLES[parent]
        if parent_rule["rule"] != "section":
            raise RuntimeError(f"Cannot stamp {table!r}: parent {parent} is not section-ruled")
        resp = db.table(parent).select("id," + parent_rule["column"]).eq(
            "id", out.get(rule["via"])).execute()
        rows = resp.data or []
        if not rows or rows[0].get(parent_rule["column"]) != scope:
            raise HTTPException(status_code=404, detail="Not found")
        return out
    raise RuntimeError(f"Table {table!r} uses rule {kind!r}, which cannot stamp creates")
