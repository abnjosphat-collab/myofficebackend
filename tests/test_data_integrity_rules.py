# tests/test_data_integrity_rules.py — the rules that keep whole categories of data bug from coming back in future work.
#
# Each test scans the routers (so it also covers code that does not exist yet) and fails with an instruction on what to do.
# They exist because the same few mistakes kept being made one module at a time: a rule checked only in the browser, an
# approver name typed by the browser, an error swallowed into a 500. See backend/docs/DATA_INTEGRITY_STANDARD.md.
import ast
import glob
import os

import pytest

from app import audit

ROUTERS = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "app", "routers", "*.py")))


def _tree(path):
    with open(path, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _source(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _model_fields(cls):
    return {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}


# ─── 1. Who did it comes from the session, never from the browser ──────────────────────────────────────────────────

IDENTITY_FIELDS = {"approved_by", "approved_at", "rejected_by", "rejected_at", "created_by", "updated_by", "sign_off_by", "sign_off_at"}


def test_request_models_never_take_who_or_when_from_the_browser():
    offenders = []
    for path in ROUTERS:
        for node in _tree(path).body:
            if isinstance(node, ast.ClassDef) and not node.name.endswith("Response"):
                bad = _model_fields(node) & IDENTITY_FIELDS
                if bad:
                    offenders.append(f"{os.path.basename(path)}: {node.name} declares {sorted(bad)}")
    assert not offenders, (
        "A request model lets the browser say who approved, created or signed something, or when. Stamp it on the server "
        "from the session instead (app.audit.decision_stamp / actor_name, or a CrudRouter before_create/before_update hook):\n  "
        + "\n  ".join(offenders)
    )


# ─── 2. An error inside a handler is never turned into a hand-written 500 ───────────────────────────────────────────

def test_no_handler_builds_its_own_500_inside_a_broad_except():
    offenders = []
    for path in ROUTERS:
        for fn in ast.walk(_tree(path)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Try):
                    continue
                for handler in node.handlers:
                    broad = handler.type is None or (isinstance(handler.type, ast.Name) and handler.type.id == "Exception")
                    if not broad:
                        continue
                    for raised in ast.walk(handler):
                        if isinstance(raised, ast.Raise) and isinstance(raised.exc, ast.Call) and getattr(raised.exc.func, "id", "") == "HTTPException":
                            code = raised.exc.args[0] if raised.exc.args else next((k.value for k in raised.exc.keywords if k.arg == "status_code"), None)
                            if isinstance(code, ast.Constant) and code.value == 500:
                                offenders.append(f"{os.path.basename(path)}:{raised.lineno} in {fn.name}")
    assert not offenders, (
        "Use `raise server_error(e, \"Doing the thing\")` (app/errors.py) instead of a hand-written 500: it keeps a deliberate "
        "404/409 raised inside the try, turns a database constraint refusal into the right 409/422, and never leaks internals:\n  "
        + "\n  ".join(offenders)
    )


# ─── 3. A record with a time range or date range has a no-overlap rule, or a written reason it does not ─────────────

RANGE_PAIRS = [("start_date", "end_date"), ("date_from", "date_to"), ("start_time", "end_time"), ("from_date", "to_date")]

# router file -> text that must appear in it, proving the rule is applied
OVERLAP_RULE_APPLIED = {
    "overtime.py": "_overlapping_request(",
    "leaves.py": "overlapping_leave(",
    "duty_roster.py": "_reject_overlap(",
    "rotation_covers.py": "_reject_overlap(",
}

# router file -> why two records may legitimately cover the same time
OVERLAP_EXEMPT = {
    "timesheets.py": "one entry per person per day (unique on employee and date); start/end are the hours within that one day",
    "daily_reports.py": "call-out times inside one daily report; a report is not a claim on someone's time",
    "contractors.py": "a contractor can run several jobs at once; jobs are not a person's time",
}


def test_every_range_record_has_an_overlap_rule_or_a_reason_it_does_not():
    missing = []
    for path in ROUTERS:
        name = os.path.basename(path)
        has_range = any(
            isinstance(node, ast.ClassDef) and not node.name.endswith("Response") and any(set(pair) <= _model_fields(node) for pair in RANGE_PAIRS)
            for node in _tree(path).body
        )
        if not has_range or name in OVERLAP_EXEMPT:
            continue
        if name not in OVERLAP_RULE_APPLIED:
            missing.append(f"{name}: has a start/end range but no overlap rule")
        elif OVERLAP_RULE_APPLIED[name] not in _source(path):
            missing.append(f"{name}: the overlap rule `{OVERLAP_RULE_APPLIED[name]}` is no longer applied")
    assert not missing, (
        "A person must not be able to hold two records for the same time. Enforce it on the SERVER (a browser warning is not a rule): "
        "see app/overtime_conflicts.py and app/leave_conflicts.py, then add the router to OVERLAP_RULE_APPLIED. If overlap is genuinely "
        "fine, add it to OVERLAP_EXEMPT with the reason:\n  " + "\n  ".join(missing)
    )


# ─── 4. Writes to the payroll and people tables are always audited ──────────────────────────────────────────────────

CORE_AUDITED = {"overtime", "leaves", "timesheets", "employees", "ppe_records"}


def test_the_payroll_and_people_tables_stay_audited():
    assert CORE_AUDITED <= audit.AUDITED_TABLES, f"These must stay in app/audit.py AUDITED_TABLES: {sorted(CORE_AUDITED - audit.AUDITED_TABLES)}"


def test_no_router_writes_an_audited_table_through_a_private_client():
    # The audit trail is recorded by the shared client in app/supabase_client.py. A router that builds its own client for an
    # audited table would write around it.
    offenders = []
    for path in ROUTERS:
        text = _source(path)
        if "create_client(" not in text:
            continue
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "table" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and arg.value in audit.AUDITED_TABLES:
                    offenders.append(f"{os.path.basename(path)}:{node.lineno} writes {arg.value} through its own client")
    assert not offenders, "Use the shared client (from app.supabase_client import supabase):\n  " + "\n  ".join(offenders)


# ─── 5. The server stamps who created or signed ─────────────────────────────────────────────────────────────────────

def test_job_card_creation_and_sign_off_are_stamped_from_the_session():
    from app.routers import job_cards

    audit.set_actor({"user_id": "u1", "email": "e@x.com", "name": "Elias Mwazha", "role": "manager"})
    try:
        assert job_cards._stamp_create({"title": "x", "created_by": "Someone Else"})["created_by"] == "Elias Mwazha"
        done = job_cards._stamp_update({"status": "completed", "sign_off_by": "Someone Else"})
        assert done["sign_off_by"] == "Elias Mwazha" and done["sign_off_at"]
        assert "sign_off_by" not in job_cards._stamp_update({"status": "in_progress"})
    finally:
        audit.set_actor(None)


def test_failure_mode_creation_is_stamped_from_the_session():
    from app.routers import failure_modes

    audit.set_actor({"user_id": "u1", "email": "e@x.com", "name": "Edson Mavhondo", "role": "manager"})
    try:
        assert failure_modes._stamp_create({"failure_mode": "x", "created_by": "Someone Else"})["created_by"] == "Edson Mavhondo"
    finally:
        audit.set_actor(None)
    assert failure_modes._stamp_create({})["created_by"] == "system"
