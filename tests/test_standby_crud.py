# tests/test_standby_crud.py — list_assignments, get_assignment, create_assignment,
# delete_assignment. test_standby_migration_fallback.py already covers
# _is_missing_column_error and update_assignment's retry-without-optional-columns
# fallback; this file covers the remaining handlers.
#
# create_assignment's payload-building has a real conditional-inclusion rule for
# the "may not exist yet until migration is run" optional columns (standby_periods,
# shift_label, shift_hours, shift_timing_periods, day_overrides) — that's the main
# branch logic under test here, distinct from update_assignment's retry-on-error
# fallback already covered elsewhere.

import pytest
from fastapi import HTTPException

import app.routers.standby as standby_mod
from app.routers.standby import (
    ShiftRosterCreate,
    list_assignments, get_assignment, create_assignment, delete_assignment,
)


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
    def order(self, *a, **k): return self
    def insert(self, data):
        self._op = "insert"
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
        if self._op == "delete":
            return _Resp(table_cfg.get("delete_return", []))
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
        monkeypatch.setattr(standby_mod, "supabase", _FakeSupabase(state, response_map))
        return state
    return _patch


def _create_payload(**overrides):
    base = dict(employee_id="E1", employee_name="John Smith", cycle_start_date="2024-06-01")
    base.update(overrides)
    return ShiftRosterCreate(**base)


# ─── list_assignments ────────────────────────────────────────────────────────────

async def test_list_assignments_returns_all_by_default(patch_supabase):
    records = [{"id": 1, "is_active": True}, {"id": 2, "is_active": False}]
    state = patch_supabase({"standby_schedules": {"select_return": records}})
    result = await list_assignments(active_only=False)
    assert result == records
    assert state["calls"][0]["filters"] == []


async def test_list_assignments_active_only_filters_on_is_active(patch_supabase):
    state = patch_supabase({"standby_schedules": {"select_return": [{"id": 1, "is_active": True}]}})
    await list_assignments(active_only=True)
    assert ("is_active", True) in state["calls"][0]["filters"]


async def test_list_assignments_empty_result_returns_empty_list_not_none(patch_supabase):
    patch_supabase({"standby_schedules": {"select_return": None}})
    result = await list_assignments(active_only=False)
    assert result == []


async def test_list_assignments_raises_500_on_db_failure(monkeypatch):
    class _RaisingQuery:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def order(self, *a, **k): return self
        def execute(self): raise Exception("db unreachable")

    class _RaisingSupabase:
        def table(self, name): return _RaisingQuery()

    monkeypatch.setattr(standby_mod, "supabase", _RaisingSupabase())
    with pytest.raises(HTTPException) as exc_info:
        await list_assignments(active_only=False)
    assert exc_info.value.status_code == 500


# ─── get_assignment ──────────────────────────────────────────────────────────────

async def test_get_assignment_found(patch_supabase):
    patch_supabase({"standby_schedules": {"select_return": [{"id": 4, "employee_name": "John Smith"}]}})
    result = await get_assignment(4)
    assert result["employee_name"] == "John Smith"


async def test_get_assignment_404_when_missing(patch_supabase):
    patch_supabase({"standby_schedules": {"select_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await get_assignment(999)
    assert exc_info.value.status_code == 404


async def test_get_assignment_raises_500_on_unexpected_db_error(monkeypatch):
    class _RaisingQuery:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def execute(self): raise Exception("db unreachable")

    class _RaisingSupabase:
        def table(self, name): return _RaisingQuery()

    monkeypatch.setattr(standby_mod, "supabase", _RaisingSupabase())
    with pytest.raises(HTTPException) as exc_info:
        await get_assignment(1)
    assert exc_info.value.status_code == 500


# ─── create_assignment ───────────────────────────────────────────────────────────

async def test_create_assignment_omits_optional_columns_when_not_provided(patch_supabase):
    state = patch_supabase({"standby_schedules": {"insert_return": [{"id": 1, "employee_id": "E1"}]}})
    await create_assignment(_create_payload(), current_user={"user_id": "u1"})
    payload = [c for c in state["calls"] if c["op"] == "insert"][0]["payload"]
    for optional_col in ("standby_periods", "shift_label", "shift_hours", "shift_timing_periods", "day_overrides"):
        assert optional_col not in payload


async def test_create_assignment_includes_optional_columns_when_provided(patch_supabase):
    state = patch_supabase({"standby_schedules": {"insert_return": [{"id": 1}]}})
    await create_assignment(
        _create_payload(
            shift_label="Night A", standby_periods=[{"day": 1}],
            shift_hours="18:00-06:00", shift_timing_periods=[{"start": "18:00"}],
            day_overrides=[{"day": "Mon"}],
        ),
        current_user={"user_id": "u1"},
    )
    payload = [c for c in state["calls"] if c["op"] == "insert"][0]["payload"]
    assert payload["shift_label"] == "Night A"
    assert payload["standby_periods"] == [{"day": 1}]
    assert payload["shift_hours"] == "18:00-06:00"
    assert payload["shift_timing_periods"] == [{"start": "18:00"}]
    assert payload["day_overrides"] == [{"day": "Mon"}]


async def test_create_assignment_raises_500_when_insert_returns_nothing(patch_supabase):
    patch_supabase({"standby_schedules": {"insert_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await create_assignment(_create_payload(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


async def test_create_assignment_raises_500_on_unexpected_db_error(monkeypatch):
    class _RaisingQuery:
        def insert(self, data): return self
        def execute(self): raise Exception("db unreachable")

    class _RaisingSupabase:
        def table(self, name): return _RaisingQuery()

    monkeypatch.setattr(standby_mod, "supabase", _RaisingSupabase())
    with pytest.raises(HTTPException) as exc_info:
        await create_assignment(_create_payload(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


# ─── delete_assignment ────────────────────────────────────────────────────────────

async def test_delete_assignment_success(patch_supabase):
    state = patch_supabase({"standby_schedules": {"select_return": [{"id": 1}]}})
    result = await delete_assignment(1, current_user={"user_id": "u1", "role": "manager"})
    assert result == {"success": True, "detail": "Assignment 1 deleted"}
    assert any(c["op"] == "delete" for c in state["calls"])


async def test_delete_assignment_404_when_missing(patch_supabase):
    patch_supabase({"standby_schedules": {"select_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await delete_assignment(999, current_user={"user_id": "u1", "role": "manager"})
    assert exc_info.value.status_code == 404


async def test_delete_assignment_raises_500_on_unexpected_db_error(monkeypatch):
    class _RaisingQuery:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def delete(self): return self
        def execute(self):
            raise Exception("db unreachable")

    class _RaisingSupabase:
        def table(self, name): return _RaisingQuery()

    monkeypatch.setattr(standby_mod, "supabase", _RaisingSupabase())
    with pytest.raises(HTTPException) as exc_info:
        await delete_assignment(1, current_user={"user_id": "u1", "role": "manager"})
    assert exc_info.value.status_code == 500


# ─── update_assignment — a non-missing-column error is NOT retried ────────────────

async def test_update_assignment_unrelated_error_is_not_treated_as_missing_column(monkeypatch):
    # _is_missing_column_error must correctly distinguish a genuine DB failure from
    # the missing-optional-column case already covered in
    # test_standby_migration_fallback.py — an unrelated error should propagate as a
    # 500, not silently retry and mask the real problem.
    from app.routers.standby import update_assignment, ShiftRosterUpdate

    # First call (_require_exists) needs a select to succeed; the update call
    # after it must fail with something other than a PGRST204/"Could not find...
    # column" message.
    calls = {"n": 0}

    class _StatefulQuery:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def update(self, data):
            calls["n"] += 1
            return self
        def execute(self):
            if calls["n"] == 0:
                return type("R", (), {"data": [{"id": 1}]})()
            raise Exception("connection reset")

    class _StatefulSupabase:
        def table(self, name): return _StatefulQuery()

    monkeypatch.setattr(standby_mod, "supabase", _StatefulSupabase())
    with pytest.raises(HTTPException) as exc_info:
        await update_assignment(1, ShiftRosterUpdate(notes="x"), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500
    assert "connection reset" in exc_info.value.detail
