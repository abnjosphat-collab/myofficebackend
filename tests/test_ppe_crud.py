# tests/test_ppe_crud.py — ppe.py's CRUD endpoints (list/create/get/update/delete +
# the per-employee listing) had zero tests beyond the already-covered stats/matrix
# logic (test_ppe_stats.py, test_ppe_matrix.py). Covers filter correctness, the
# exclude_unset null-clearing on update (see work_orders backend edec24a), 404s, and
# date-field isoformatting on create/update.

import pytest

import app.routers.ppe as ppe_mod
from app.routers.ppe import (
    PPEIssueCreate, PPEIssueUpdate,
    get_ppe_records, create_ppe_record, get_ppe_record, update_ppe_record,
    delete_ppe_record, get_employee_ppe_records,
)
from fastapi import HTTPException


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state):
        self.table_name = table_name
        self.state = state
        self._filters = []
        self._order = None
        self._range = None
        self._mode = "select"
        self._payload = None

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def ilike(self, col, val):
        # The register stamp matches case-insensitively, verified exactly in Python; the fake emulates the composed result.
        self._filters.append((col, val, "ilike"))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def insert(self, data):
        self._mode = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._mode = "update"
        self._payload = data
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def execute(self):
        table = self.state.setdefault(self.table_name, [])
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "mode": self._mode, "filters": list(self._filters), "payload": self._payload}
        )
        if self._mode == "insert":
            row = dict(self._payload)
            next_id = self.state.setdefault("_next_id", [1])
            row["id"] = next_id[0]
            next_id[0] += 1
            table.append(row)
            return _Resp([row])

        def _hit(row, f):
            if len(f) == 2:
                col, val = f
                return row.get(col) == val
            col, val, _op = f
            return str(row.get(col) or "").upper() == str(val or "").upper()

        matches = [r for r in table if all(_hit(r, f) for f in self._filters)]

        if self._mode == "select":
            if self._order:
                col, desc = self._order
                matches = sorted(matches, key=lambda r: r.get(col) or "", reverse=desc)
            if self._range:
                start, end = self._range
                matches = matches[start:end + 1]
            return _Resp(matches)
        if self._mode == "update":
            for r in matches:
                r.update(self._payload)
            return _Resp(matches)
        if self._mode == "delete":
            for r in matches:
                table.remove(r)
            return _Resp(matches)
        return _Resp([])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(name, self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {}

    def _patch(ppe_records=None, employees=None):
        state["ppe_records"] = list(ppe_records or [])
        state["employees"] = list(employees) if employees is not None else [
            {"employee_id": "E1", "first_name": "J.", "last_name": "Doe",
             "designation": "Fitter", "department": "Ops", "section": "Mechanical"},
        ]
        monkeypatch.setattr(ppe_mod, "supabase", _FakeSupabase(state))
        return state

    return _patch


def _user():
    return {"user_id": "u-1", "email": "u@x.com", "role": "user"}


def _manager():
    return {"user_id": "m-1", "email": "m@x.com", "role": "manager"}


# ─── get_ppe_records ─────────────────────────────────────────────────────────────────

async def test_list_no_filters_returns_all(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "A", "employee_id": "E1"},
        {"id": 2, "status": "expired", "ppe_type": "gloves", "department": "Eng",
         "location": "B", "employee_id": "E2"},
    ])
    result = await get_ppe_records(status=None, ppe_type=None, department=None, location=None, employee_id=None)
    assert len(result) == 2


async def test_list_filters_by_ppe_type(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "A", "employee_id": "E1"},
        {"id": 2, "status": "active", "ppe_type": "gloves", "department": "Ops",
         "location": "A", "employee_id": "E1"},
    ])
    result = await get_ppe_records(status=None, ppe_type="gloves", department=None, location=None, employee_id=None)
    assert [r["id"] for r in result] == [2]


async def test_list_filters_by_status(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "A", "employee_id": "E1"},
        {"id": 2, "status": "expired", "ppe_type": "helmet", "department": "Ops",
         "location": "A", "employee_id": "E1"},
    ])
    result = await get_ppe_records(status="expired", ppe_type=None, department=None, location=None, employee_id=None)
    assert [r["id"] for r in result] == [2]


async def test_list_filters_by_department(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "A", "employee_id": "E1"},
        {"id": 2, "status": "active", "ppe_type": "helmet", "department": "Eng",
         "location": "A", "employee_id": "E1"},
    ])
    result = await get_ppe_records(status=None, ppe_type=None, department="Eng", location=None, employee_id=None)
    assert [r["id"] for r in result] == [2]


async def test_list_filters_by_location(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "Site A", "employee_id": "E1"},
        {"id": 2, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "Site B", "employee_id": "E1"},
    ])
    result = await get_ppe_records(status=None, ppe_type=None, department=None, location="Site B", employee_id=None)
    assert [r["id"] for r in result] == [2]


async def test_list_filters_by_employee_id(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "A", "employee_id": "E1"},
        {"id": 2, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "A", "employee_id": "E2"},
    ])
    result = await get_ppe_records(status=None, ppe_type=None, department=None, location=None, employee_id="E2")
    assert [r["id"] for r in result] == [2]


async def test_list_value_all_is_not_filtered(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "A", "employee_id": "E1"},
        {"id": 2, "status": "expired", "ppe_type": "gloves", "department": "Eng",
         "location": "B", "employee_id": "E2"},
    ])
    result = await get_ppe_records(status="all", ppe_type=None, department=None, location=None, employee_id=None)
    assert len(result) == 2


async def test_list_pages_past_supabase_default_limit(patch_supabase):
    patch_supabase([
        {"id": index, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "A", "employee_id": f"E{index}"}
        for index in range(1, 1002)
    ])
    result = await get_ppe_records(status=None, ppe_type=None, department=None, location=None, employee_id=None)
    assert len(result) == 1001
    assert {row["id"] for row in result} == set(range(1, 1002))


# ─── create_ppe_record ───────────────────────────────────────────────────────────────

async def test_create_happy_path(patch_supabase):
    patch_supabase()
    record = PPEIssueCreate(
        employee_name="J. Doe", employee_id="E1", department="Ops", position="Fitter",
        ppe_type="helmet", item_name="Hard Hat", issue_date="2026-08-01",
    )
    result = await create_ppe_record(record, current_user=_user())
    assert result["employee_name"] == "J. Doe"
    assert result["issue_date"] == "2026-08-01"
    assert result["condition"] == "good"   # model default
    assert result["status"] == "active"    # model default
    assert result["mine_section"] == "Mechanical"   # stamped from the register, never posted
    assert "created_at" in result


async def test_create_isoformats_expiry_date(patch_supabase):
    patch_supabase()
    record = PPEIssueCreate(
        employee_name="J. Doe", employee_id="E1", department="Ops", position="Fitter",
        ppe_type="helmet", item_name="Hard Hat", issue_date="2026-08-01", expiry_date="2027-02-01",
    )
    result = await create_ppe_record(record, current_user=_user())
    assert result["expiry_date"] == "2027-02-01"


async def test_create_stamps_person_from_register_ignoring_client_copies(patch_supabase):
    patch_supabase()
    record = PPEIssueCreate(
        employee_name="Wrong Name", employee_id="E1", department="Wrong", position="Wrong",
        ppe_type="helmet", item_name="Hard Hat", issue_date="2026-08-01", mine_section="Wrong",
    )
    result = await create_ppe_record(record, current_user=_user())
    assert result["employee_name"] == "J. Doe"
    assert result["position"] == "Fitter"
    assert result["department"] == "Ops"
    assert result["mine_section"] == "Mechanical"


async def test_create_unknown_mine_number_is_422(patch_supabase):
    patch_supabase()
    record = PPEIssueCreate(employee_id="E999", ppe_type="helmet", item_name="Hard Hat", issue_date="2026-08-01")
    with pytest.raises(HTTPException) as exc:
        await create_ppe_record(record, current_user=_user())
    assert exc.value.status_code == 422
    assert "not on the employee register" in exc.value.detail


async def test_create_lowercase_id_stores_register_spelling(patch_supabase):
    patch_supabase()
    record = PPEIssueCreate(employee_id="e1", ppe_type="helmet", item_name="Hard Hat", issue_date="2026-08-01")
    result = await create_ppe_record(record, current_user=_user())
    assert result["employee_id"] == "E1"


async def test_create_register_lookup_failure_is_500_not_422(patch_supabase, monkeypatch):
    patch_supabase()

    def _boom(_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(ppe_mod, "_register_employee", _boom)
    record = PPEIssueCreate(employee_id="E1", ppe_type="helmet", item_name="Hard Hat", issue_date="2026-08-01")
    with pytest.raises(HTTPException) as exc:
        await create_ppe_record(record, current_user=_user())
    assert exc.value.status_code == 500


# ─── get_ppe_record ──────────────────────────────────────────────────────────────────

async def test_get_single_found(patch_supabase):
    patch_supabase([{"id": 5, "employee_name": "A"}])
    result = await get_ppe_record(5)
    assert result["id"] == 5


async def test_get_single_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await get_ppe_record(999)
    assert exc.value.status_code == 404


# ─── update_ppe_record ───────────────────────────────────────────────────────────────

async def test_update_changes_only_sent_fields(patch_supabase):
    state = patch_supabase([{"id": 7, "status": "active", "condition": "good"}])
    result = await update_ppe_record(7, PPEIssueUpdate(status="replaced"), current_user=_user())
    assert result["status"] == "replaced"
    assert result["condition"] == "good"  # untouched
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert "condition" not in update_calls[0]["payload"]


async def test_update_explicit_null_clears_field(patch_supabase):
    state = patch_supabase([{"id": 7, "status": "active", "expiry_date": "2026-09-01"}])
    result = await update_ppe_record(7, PPEIssueUpdate(expiry_date=None), current_user=_user())
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert update_calls[0]["payload"]["expiry_date"] is None  # explicitly cleared, not dropped


async def test_update_isoformats_dates_when_sent(patch_supabase):
    state = patch_supabase([{"id": 7, "status": "active"}])
    await update_ppe_record(7, PPEIssueUpdate(issue_date="2026-08-15"), current_user=_user())
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert update_calls[0]["payload"]["issue_date"] == "2026-08-15"


async def test_update_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await update_ppe_record(404, PPEIssueUpdate(status="replaced"), current_user=_user())
    assert exc.value.status_code == 404


async def test_update_move_to_another_person_restamps_from_register(patch_supabase):
    patch_supabase(
        [{"id": 7, "employee_id": "E1", "employee_name": "J. Doe", "status": "active"}],
        employees=[
            {"employee_id": "E1", "first_name": "J.", "last_name": "Doe",
             "designation": "Fitter", "department": "Ops", "section": "Mechanical"},
            {"employee_id": "E2", "first_name": "A.", "last_name": "Nother",
             "designation": "Welder", "department": "Eng", "section": "Electrical"},
        ],
    )
    result = await update_ppe_record(7, PPEIssueUpdate(employee_id="E2"), current_user=_user())
    assert result["employee_id"] == "E2"
    assert result["employee_name"] == "A. Nother"
    assert result["position"] == "Welder"
    assert result["department"] == "Eng"
    assert result["mine_section"] == "Electrical"


async def test_update_move_to_unknown_person_is_422(patch_supabase):
    patch_supabase([{"id": 7, "employee_id": "E1", "status": "active"}])
    with pytest.raises(HTTPException) as exc:
        await update_ppe_record(7, PPEIssueUpdate(employee_id="E999"), current_user=_user())
    assert exc.value.status_code == 422
    assert "not on the employee register" in exc.value.detail


async def test_update_same_person_keeps_stored_spelling_and_skips_lookup(patch_supabase):
    state = patch_supabase([{"id": 7, "employee_id": "E1", "status": "active"}])
    result = await update_ppe_record(7, PPEIssueUpdate(employee_id="e1", notes="x"), current_user=_user())
    assert result["employee_id"] == "E1"
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert "employee_id" not in update_calls[0]["payload"]
    assert [c for c in state["calls"] if c["table"] == "employees"] == []


async def test_update_without_person_skips_lookup(patch_supabase):
    state = patch_supabase([{"id": 7, "employee_id": "E1", "status": "active", "department": "Ops"}])
    result = await update_ppe_record(7, PPEIssueUpdate(status="returned"), current_user=_user())
    assert result["status"] == "returned"
    assert [c for c in state["calls"] if c["table"] == "employees"] == []


# ─── delete_ppe_record ───────────────────────────────────────────────────────────────

async def test_delete_happy_path(patch_supabase):
    state = patch_supabase([{"id": 3, "status": "active"}])
    result = await delete_ppe_record(3, current_user=_manager())
    assert result == {"success": True, "message": "PPE record deleted successfully"}
    assert state["ppe_records"] == []


async def test_delete_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await delete_ppe_record(404, current_user=_manager())
    assert exc.value.status_code == 404


# ─── get_employee_ppe_records ────────────────────────────────────────────────────────

async def test_employee_records_filters_and_sorts_by_issue_date_desc(patch_supabase):
    patch_supabase([
        {"id": 1, "employee_id": "E1", "issue_date": "2026-01-01"},
        {"id": 2, "employee_id": "E2", "issue_date": "2026-06-01"},
        {"id": 3, "employee_id": "E1", "issue_date": "2026-07-01"},
    ])
    result = await get_employee_ppe_records("E1")
    assert [r["id"] for r in result] == [3, 1]


async def test_employee_records_no_matches_is_empty_list(patch_supabase):
    patch_supabase([{"id": 1, "employee_id": "E1", "issue_date": "2026-01-01"}])
    result = await get_employee_ppe_records("E999")
    assert result == []
