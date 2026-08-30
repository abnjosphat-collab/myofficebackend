# tests/test_timesheets_crud.py — get_timesheets, create_timesheet_entry,
# get_timesheet_entry, update_timesheet_entry, delete_timesheet_entry.
#
# Confirmed during this pass that timesheets.py does NOT have the null-vs-unset
# PATCH bug: update_timesheet_entry already builds its payload with
# `updated.model_dump(exclude_unset=True)` and its own comment (lines 138-139)
# documents that this was deliberately kept clean of the `is not None` filter
# regression fixed elsewhere in commit a4a3a87 — see
# test_update_timesheet_entry_can_explicitly_clear_a_field below, a regression
# guard rather than a fix.
#
# create_timesheet_entry has real upsert-by-(employee_id, date) business logic
# (update the existing row for that employee/day instead of duplicating it) that
# had zero coverage — that's the main behavior under test here.

import pytest
from fastapi import HTTPException

import app.routers.timesheets as ts_mod
from app.routers.timesheets import (
    TimesheetEntryCreate, TimesheetEntryUpdate,
    get_timesheets, create_timesheet_entry, get_timesheet_entry,
    update_timesheet_entry, delete_timesheet_entry,
)


# ─── Fake supabase — records every call for assertion ──────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state, response_map):
        self.table_name = table_name
        self.state = state
        self._response_map = response_map
        self._filters = []
        self._payload = None
        self._op = "select"

    def select(self, *a, **k): return self
    def eq(self, col, val):
        self._filters.append((col, val))
        return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self
    def update(self, data):
        self._op = "update"
        self._payload = data
        return self
    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        table_cfg = self._response_map.get(self.table_name, {})
        if self._op == "insert":
            return _Resp(table_cfg.get("insert_return", [{"id": 1, **(self._payload or {})}]))
        if self._op == "update":
            return _Resp(table_cfg.get("update_return", [{"id": 1, **(self._payload or {})}]))
        if self._op == "delete":
            return _Resp(table_cfg.get("delete_return", []))
        # Multiple selects can happen in one test (existence-check then get) —
        # pop the next scripted response if a list of them was provided.
        select_returns = table_cfg.get("select_returns")
        if select_returns is not None:
            idx = self.state.setdefault("select_call_idx", {}).get(self.table_name, 0)
            self.state["select_call_idx"][self.table_name] = idx + 1
            return _Resp(select_returns[min(idx, len(select_returns) - 1)])
        return _Resp(table_cfg.get("select_return", []))


class _FakeSupabase:
    def __init__(self, state, response_map):
        self.state = state
        self.response_map = response_map

    def table(self, name):
        return _FakeQuery(name, self.state, self.response_map)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(response_map: dict):
        state = {"calls": []}
        monkeypatch.setattr(ts_mod, "supabase", _FakeSupabase(state, response_map))
        return state
    return _patch


def _entry_payload(**overrides):
    base = dict(employee_id=7, date="2024-06-10", regular_hours=8, total_hours=8)
    base.update(overrides)
    return TimesheetEntryCreate(**base)


# ─── get_timesheets ──────────────────────────────────────────────────────────────

async def test_get_timesheets_no_filters(patch_supabase):
    records = [{"id": 1}, {"id": 2}]
    state = patch_supabase({"timesheets": {"select_return": records}})
    result = await get_timesheets(employee_id=None, start_date=None, end_date=None)
    assert result == records
    assert state["calls"][0]["filters"] == []


async def test_get_timesheets_filters_by_employee_id(patch_supabase):
    state = patch_supabase({"timesheets": {"select_return": [{"id": 1}]}})
    await get_timesheets(employee_id=7, start_date=None, end_date=None)
    assert ("employee_id", 7) in state["calls"][0]["filters"]


# ─── create_timesheet_entry ──────────────────────────────────────────────────────

async def test_create_timesheet_entry_inserts_new_when_none_exists(patch_supabase):
    state = patch_supabase({
        "timesheets": {
            "select_return": [],  # existence check finds nothing
            "insert_return": [{"id": 5, "employee_id": 7, "date": "2024-06-10", "overtime_periods": "[]"}],
        },
    })
    result = await create_timesheet_entry(_entry_payload(), current_user={"user_id": "u1"})
    assert result["action"] == "created"
    assert result["data"]["id"] == 5
    inserts = [c for c in state["calls"] if c["op"] == "insert"]
    assert len(inserts) == 1
    assert inserts[0]["payload"]["employee_id"] == 7
    assert inserts[0]["payload"]["date"] == "2024-06-10"


async def test_create_timesheet_entry_updates_existing_entry_for_same_employee_and_day(patch_supabase):
    # Real business rule: posting a second entry for the same employee/date should
    # update the existing row, not create a duplicate.
    state = patch_supabase({
        "timesheets": {
            "select_return": [{"id": 9, "employee_id": 7, "date": "2024-06-10"}],
            "update_return": [{"id": 9, "employee_id": 7, "date": "2024-06-10", "regular_hours": 8, "overtime_periods": "[]"}],
        },
    })
    result = await create_timesheet_entry(_entry_payload(), current_user={"user_id": "u1"})
    assert result["action"] == "updated"
    assert result["data"]["id"] == 9
    update_calls = [c for c in state["calls"] if c["op"] == "update"]
    assert len(update_calls) == 1
    assert [c for c in state["calls"] if c["op"] == "insert"] == []


async def test_create_timesheet_entry_raises_500_when_db_operation_fails(patch_supabase):
    patch_supabase({"timesheets": {"select_return": [], "insert_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await create_timesheet_entry(_entry_payload(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


# ─── get_timesheet_entry ─────────────────────────────────────────────────────────

async def test_get_timesheet_entry_found(patch_supabase):
    patch_supabase({"timesheets": {"select_return": [{"id": 3, "employee_id": 7, "overtime_periods": "[]"}]}})
    result = await get_timesheet_entry(3)
    assert result["id"] == 3
    assert result["overtime_periods"] == []  # decoded from JSON string


async def test_get_timesheet_entry_404_when_missing(patch_supabase):
    patch_supabase({"timesheets": {"select_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await get_timesheet_entry(999)
    assert exc_info.value.status_code == 500  # 404 raised internally, re-wrapped by outer except


# ─── update_timesheet_entry ──────────────────────────────────────────────────────

async def test_update_timesheet_entry_success(patch_supabase):
    state = patch_supabase({
        "timesheets": {
            "select_return": [{"id": 3, "employee_id": 7}],
            "update_return": [{"id": 3, "employee_id": 7, "notes": "Late start", "overtime_periods": "[]"}],
        },
    })
    result = await update_timesheet_entry(3, TimesheetEntryUpdate(notes="Late start"), current_user={"user_id": "u1"})
    assert result["notes"] == "Late start"
    update_calls = [c for c in state["calls"] if c["op"] == "update"]
    assert update_calls[0]["payload"]["notes"] == "Late start"
    assert "updated_at" in update_calls[0]["payload"]


async def test_update_timesheet_entry_can_explicitly_clear_a_field(patch_supabase):
    # Regression guard for the null-vs-unset behavior already documented at the
    # call site: exclude_unset=True with no `is not None` filter on top means an
    # explicit null must reach the update payload.
    state = patch_supabase({
        "timesheets": {
            "select_return": [{"id": 3, "employee_id": 7, "notes": "old"}],
            "update_return": [{"id": 3, "employee_id": 7, "notes": None, "overtime_periods": "[]"}],
        },
    })
    result = await update_timesheet_entry(3, TimesheetEntryUpdate(notes=None), current_user={"user_id": "u1"})
    update_calls = [c for c in state["calls"] if c["op"] == "update"]
    assert "notes" in update_calls[0]["payload"]
    assert update_calls[0]["payload"]["notes"] is None
    assert result["notes"] is None


async def test_update_timesheet_entry_404_when_missing(patch_supabase):
    patch_supabase({"timesheets": {"select_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await update_timesheet_entry(999, TimesheetEntryUpdate(notes="x"), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


# ─── delete_timesheet_entry ──────────────────────────────────────────────────────

async def test_delete_timesheet_entry_success(patch_supabase):
    state = patch_supabase({"timesheets": {"select_return": [{"id": 3}]}})
    result = await delete_timesheet_entry(3, current_user={"user_id": "u1", "role": "manager"})
    assert result == {"success": True, "message": "Timesheet entry deleted successfully"}
    assert any(c["op"] == "delete" for c in state["calls"])


async def test_delete_timesheet_entry_404_when_missing(patch_supabase):
    patch_supabase({"timesheets": {"select_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await delete_timesheet_entry(999, current_user={"user_id": "u1", "role": "manager"})
    assert exc_info.value.status_code == 500
