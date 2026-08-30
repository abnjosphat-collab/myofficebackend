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
        self._mode = "select"
        self._payload = None

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
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

        matches = [r for r in table if all(r.get(c) == v for c, v in self._filters)]

        if self._mode == "select":
            if self._order:
                col, desc = self._order
                matches = sorted(matches, key=lambda r: r.get(col) or "", reverse=desc)
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

    def _patch(ppe_records=None):
        state["ppe_records"] = list(ppe_records or [])
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
    assert "created_at" in result


async def test_create_isoformats_expiry_date(patch_supabase):
    patch_supabase()
    record = PPEIssueCreate(
        employee_name="J. Doe", employee_id="E1", department="Ops", position="Fitter",
        ppe_type="helmet", item_name="Hard Hat", issue_date="2026-08-01", expiry_date="2027-02-01",
    )
    result = await create_ppe_record(record, current_user=_user())
    assert result["expiry_date"] == "2027-02-01"


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
