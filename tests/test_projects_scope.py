# tests/test_projects_scope.py — standing acceptance suite for the section-scope
# feature ("account limited to one section"). Batch 0: RED — no enforcement and
# no app/scope.py yet. Each batch greens its slice; all green at the end.
#
# Approach: real HTTP through TestClient with get_current_user overridden per
# role, against an in-memory fake Supabase that HONOURS equality filters (so it
# behaves like the database once routers add scope predicates, and leaks like
# the database today). Fixture: two Projects people + one Mechanical person,
# with records for each link rule. The scoped user contract Batch 1 must
# implement: current_user["permissions"]["scope"] == "Projects".
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from main import app


# --------------------------------------------------------------------------
# In-memory Supabase fake (honours filters)
# --------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = len(data) if count == "exact" else count


class _FakeQuery:
    def __init__(self, rows, calls):
        self._rows = [dict(r) for r in rows]
        self._calls = calls
        self._writes = None
        self._count = None

    # -- filters -----------------------------------------------------------
    def select(self, cols="*", count=None):
        self._count = count
        return self

    def eq(self, col, val):
        self._calls.append(("eq", col, val))
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def neq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) != val]
        return self

    def in_(self, col, vals):
        self._calls.append(("in", col, list(vals)))
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self

    def like(self, col, pat):
        return self.ilike(col, pat)

    def ilike(self, col, pat):
        needle = str(pat).strip("%").lower()
        self._rows = [r for r in self._rows if needle in str(r.get(col) or "").lower()]
        return self

    def gte(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) is not None and r.get(col) >= val]
        return self

    def lte(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) is not None and r.get(col) <= val]
        return self

    def gt(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) is not None and r.get(col) > val]
        return self

    def lt(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) is not None and r.get(col) < val]
        return self

    def or_(self, _expr):
        # Search expressions are out of this suite's reach: no test sends a
        # search term, so an unfiltered pass-through is faithful here.
        return self

    def order(self, col, desc=False):
        self._rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=bool(desc))
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def offset(self, n):
        self._rows = self._rows[n:]
        return self

    def range(self, start, end):
        self._rows = self._rows[start:end + 1]
        return self

    def single(self):
        return self

    # -- writes ------------------------------------------------------------
    def insert(self, payload):
        rows = payload if isinstance(payload, list) else [payload]
        self._writes = ("insert", [dict(r) for r in rows])
        return self

    def update(self, payload):
        self._writes = ("update", dict(payload))
        return self

    def delete(self):
        self._writes = ("delete", None)
        return self

    def execute(self):
        return _FakeResult(self._rows, self._count)


class _FakeSupabase:
    """Per-table stores. Writes apply to the store so creates are visible."""

    def __init__(self, store, calls):
        self._store = store
        self._calls = calls

    def table(self, name):
        store, calls = self._store, self._calls
        inner = _FakeQuery(store.setdefault(name, []), calls)

        orig_execute = inner.execute

        def execute():
            if inner._writes:
                op, payload = inner._writes
                rows = store.setdefault(name, [])
                if op == "insert":
                    for row in payload:
                        if "id" not in row:
                            row["id"] = (max([r.get("id", 0) for r in rows] or [0]) or 0) + 1
                        rows.append(row)
                    return _FakeResult(payload)
                for row in rows:
                    if row in inner._rows or any(
                        row.get(k) == v for k, v in [c[1:] for c in calls if c[0] == "eq"]
                    ):
                        if op == "update":
                            row.update(payload)
                if op == "delete":
                    store[name] = [r for r in rows if r not in inner._rows]
                    return _FakeResult([])
                return _FakeResult([r for r in rows if r in inner._rows])
            return orig_execute()

        inner.execute = execute  # noqa: B010 - deliberate test-double seam
        return inner


# --------------------------------------------------------------------------
# Fixture: Projects + Mechanical people and records, three users
# --------------------------------------------------------------------------

ADMIN = {"sub": "admin-1", "email": "admin@example.com", "role": "admin", "scope": None}
MANAGER = {"sub": "mgr-1", "email": "mgr@example.com", "role": "manager", "scope": None}
SCOPED = {"sub": "user-1", "email": "scoped@example.com", "role": "user",
          "scope": "Projects"}


def _store():
    return {
        "employees": [
            {"id": 1, "employee_id": "C1001", "first_name": "Pam", "last_name": "Projects",
             "id_number": "ID1", "designation": "Fitter", "department": "Engineering",
             "section": "Projects"},
            {"id": 2, "employee_id": "C1002", "first_name": "Pat", "last_name": "Projects",
             "id_number": "ID2", "designation": "Boilermaker", "department": "Engineering",
             "section": "Projects"},
            {"id": 3, "employee_id": "C2001", "first_name": "Max", "last_name": "Mechanical",
             "id_number": "ID3", "designation": "Fitter", "department": "Engineering",
             "section": "Mechanical"},
        ],
        "leaves": [
            {"id": 11, "employee_id": "C1001", "employee_name": "Pam Projects",
             "position": "Fitter", "contact_number": "1", "leave_type": "annual",
             "start_date": "2026-09-01", "end_date": "2026-09-02", "total_days": 2,
             "reason": "Rest", "emergency_contact": None, "handover_to": None,
             "status": "pending", "applied_date": "2026-08-20T00:00:00",
             "updated_at": None, "notes": None},
            {"id": 12, "employee_id": "C2001", "employee_name": "Max Mechanical",
             "position": "Fitter", "contact_number": "2", "leave_type": "annual",
             "start_date": "2026-09-01", "end_date": "2026-09-02", "total_days": 2,
             "reason": "Rest", "emergency_contact": None, "handover_to": None,
             "status": "pending", "applied_date": "2026-08-20T00:00:00",
             "updated_at": None, "notes": None},
        ],
        "nearmiss_reports": [
            {"id": 21, "section": "Projects", "title": "Loose cable"},
            {"id": 22, "section": "Mechanical", "title": "Oil spill"},
        ],
        "equipment": [
            {"id": 31, "name": "Mill 1", "section": "Projects"},
            {"id": 32, "name": "Mill 2", "section": "Mechanical"},
        ],
        "sheq_inspections": [
            {"id": 41, "section": "Projects", "title": "Shaft check"},
            {"id": 42, "section": "Mechanical", "title": "Plant check"},
        ],
        "sheq_findings": [
            {"id": 51, "inspection_id": 41, "detail": "Guard missing"},
            {"id": 52, "inspection_id": 42, "detail": "Wire exposed"},
        ],
    }


@pytest.fixture()
def scope_client(monkeypatch):
    """TestClient with auth overridden per test and router modules on the fake."""
    import app.routers.employees as emp_mod
    import app.routers.leaves as leaves_mod
    import app.routers.near_miss as near_mod
    import app.routers.equipment as equip_mod
    import app.routers.sheq_inspections as sheq_mod

    store, calls = _store(), []
    fake = _FakeSupabase(store, calls)
    _current = [ADMIN]

    async def _stub_user(authorization=None):
        return _current[0]

    for mod in (emp_mod, leaves_mod, near_mod, equip_mod, sheq_mod):
        monkeypatch.setattr(mod, "supabase", fake)
        monkeypatch.setattr(mod, "get_current_user", _stub_user)
    # The conditional approve/reject gate calls get_current_user through the
    # auth module's own binding, which bypasses dependency_overrides too.
    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_current_user", _stub_user)

    async def _override():
        return _current[0]

    app.dependency_overrides[get_current_user] = _override
    client = TestClient(app, raise_server_exceptions=False)
    client.scope_as = lambda user: _current.__setitem__(0, user)  # noqa: E731
    client.scope_store = store
    client.scope_calls = calls
    yield client
    app.dependency_overrides.pop(get_current_user, None)


def _rows(resp):
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if isinstance(body, dict):
        for key in ("data", "items", "rows", "results"):
            if key in body:
                return body[key]
    assert isinstance(body, list), body
    return body


# --------------------------------------------------------------------------
# The guard: no scoped-table read without app/scope.py (acceptance criterion)
# --------------------------------------------------------------------------

SCOPED_TABLES = {
    # Batch 2 — people
    "employees", "leaves", "overtime", "timesheets", "artisan_timesheets",
    "ppe_records", "ppe_order_list", "stock_issues", "training_certifications",
    "competency_matrix", "standby_schedules", "duty_roster", "standby_rotations",
    "duty_rotations", "rotation_covers", "shift_handovers", "job_cards",
    # Batch 3 — direct section
    "nearmiss_reports", "sheq_inspections", "sheq_findings",
    "work_stoppage_reports", "pto_reports", "pto_action_plan", "requisitions",
    "requisition_items", "safety_complaints", "lube_schedules",
    # Batch 4 — new column / inherit
    "equipment", "work_orders", "breakdowns", "notices", "sop_documents",
    "sop_revisions", "services", "service_attachments", "daily_reports",
    "pachedu_reports", "vfl_reports", "vfl_action_plan", "maintenance_schedules",
    "availabilities", "corrective_actions", "compliance", "condition_monitoring",
    # Batch 5 — documents
    "documents", "document_folders", "document_activities",
}

# (file, table) pairs whose read provably serves no scoped rows.
EXEMPT_READS = {
    ("app/routers/overtime.py", "equipment"):
        "reason-text cross-match against register names; names never served",
    ("app/leave_conflicts.py", "leaves"):
        "pure helper over a caller-supplied person; leaves.py stamps first, "
        "overtime take-as-leave asserts the OT row first",
    ("app/ppe_conflicts.py", "ppe_records"):
        "pure helper over caller-supplied person+type; ppe.py checks the "
        "person's section before both duplicate checks",
    ("app/nec_import/apply_runner.py", "employees"):
        "only runs from apply_import, which 403s scoped callers first",
    ("app/nec_import/apply_runner.py", "leaves"):
        "only runs from apply_import, which 403s scoped callers first",
    ("app/nec_import/apply_runner.py", "timesheets"):
        "only runs from apply_import, which 403s scoped callers first",
}


def _tables_read(src):
    found = set(re.findall(r'\.(?:table|from_)\(\s*["\']([a-z_][a-z0-9_]*)["\']', src))
    found.update(re.findall(r'CrudRouter\(\s*["\']([a-z_][a-z0-9_]*)["\']', src))
    m = re.search(r'\bTABLE\s*=\s*["\']([a-z_][a-z0-9_]*)["\']', src)
    if m:
        found.add(m.group(1))
    return found


def test_scoped_table_reads_go_through_scope_registry():
    """A new endpoint reading a scoped table without app/scope.py fails here.
    Reads through an opted-in CrudRouter (scope_table=) count: the generic
    layer enforces on the router's behalf."""
    root = Path("app")
    offenders = []
    for path in sorted(root.rglob("*.py")):
        rel = path.as_posix()
        if rel == "app/scope.py":
            continue
        src = path.read_text(encoding="utf-8")
        uses_scope = "app.scope" in src or "from app import scope" in src or "scope_table=" in src
        for table in sorted(_tables_read(src) & SCOPED_TABLES):
            if (rel, table) in EXEMPT_READS or uses_scope:
                continue
            offenders.append(f"{rel} reads {table} without app/scope.py")
    assert not offenders, "\n".join(offenders)


def test_scope_registry_matches_inventory():
    """app/scope.py (Batch 1) must cover every inventoried table."""
    import app.scope  # noqa: F401 — ModuleNotFoundError until Batch 1

    assert SCOPED_TABLES <= set(app.scope.TABLES)


# --------------------------------------------------------------------------
# Behaviour: scoped user (fails now — nothing is enforced)
# --------------------------------------------------------------------------

def test_scoped_employee_list_sees_only_projects(scope_client):
    scope_client.scope_as(SCOPED)
    ids = [r["employee_id"] for r in _rows(scope_client.get("/api/employees"))]
    assert ids == ["C1001", "C1002"]


def test_scoped_employee_read_of_mechanical_is_404(scope_client):
    scope_client.scope_as(SCOPED)
    assert scope_client.get("/api/employees/3").status_code == 404
    assert scope_client.get("/api/employees/1").status_code == 200


def test_scoped_leave_list_sees_only_projects_people(scope_client):
    scope_client.scope_as(SCOPED)
    ids = [r["employee_id"] for r in _rows(scope_client.get("/api/leaves"))]
    assert ids == ["C1001"]


def test_scoped_employee_create_is_forced_to_projects(scope_client):
    scope_client.scope_as(SCOPED)
    resp = scope_client.post("/api/employees", json={
        "employee_id": "C1003", "first_name": "New", "last_name": "Hire",
        "id_number": "ID9", "designation": "Fitter", "department": "Engineering",
        "section": "Mechanical",
    })
    assert resp.status_code in (200, 201), resp.text
    created = [r for r in scope_client.scope_store["employees"] if r["employee_id"] == "C1003"]
    assert created and created[0]["section"] == "Projects"


def test_scoped_nearmiss_list_sees_only_projects(scope_client):
    scope_client.scope_as(SCOPED)
    ids = [r["id"] for r in _rows(scope_client.get("/api/nearmiss"))]
    assert ids == [21]


def test_scoped_equipment_list_sees_only_projects(scope_client):
    scope_client.scope_as(SCOPED)
    ids = [r["id"] for r in _rows(scope_client.get("/api/equipment"))]
    assert ids == [31]


def test_scoped_findings_of_out_of_scope_inspection_are_404(scope_client):
    scope_client.scope_as(SCOPED)
    assert scope_client.get("/api/sheq/42/findings").status_code == 404
    found = _rows(scope_client.get("/api/sheq/41/findings"))
    assert [r["id"] for r in found] == [51]


def test_scoped_and_admin_do_not_share_cached_responses(scope_client):
    first = _rows(scope_client.get("/api/employees"))
    assert len(first) == 3
    scope_client.scope_as(SCOPED)
    second = [r["employee_id"] for r in _rows(scope_client.get("/api/employees"))]
    assert second == ["C1001", "C1002"]


# --------------------------------------------------------------------------
# Behaviour that must never change (green now, green forever)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("user", [ADMIN, MANAGER])
@pytest.mark.parametrize("path,marker", [
    ("/api/employees", "C2001"),
    ("/api/leaves", "C2001"),
    ("/api/nearmiss", 22),
    ("/api/equipment", 32),
])
def test_unscoped_responses_are_unchanged(scope_client, user, path, marker):
    scope_client.scope_as(user)
    body = scope_client.get(path).json()
    flat = repr(body)
    assert str(marker) in flat, f"{path} lost its Mechanical row for {user['role']}"


def test_approvals_still_require_manager(scope_client):
    scope_client.scope_as(SCOPED)
    resp = scope_client.patch("/api/leaves/11", json={"status": "approved"})
    assert resp.status_code == 403, resp.text
