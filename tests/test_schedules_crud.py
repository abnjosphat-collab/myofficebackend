# tests/test_schedules_crud.py — schedules.py's plain CRUD handlers (list_schedules,
# create_schedule, update_schedule, delete_schedule, schedule_runs). The pure
# recurrence-calculation engine (_clamp_dom/next_occurrence/_as_dates/is_due) is
# already thoroughly covered in test_schedules_recurrence.py; generation
# (_generate_one/generate_due_work_orders) is covered separately in
# test_schedules_generation.py since it's meaty enough to deserve its own file.
#
# Uses the same "call the route coroutine directly against a fake supabase client"
# recipe as test_vfl_reports.py/test_pto_reports.py — bespoke per file since this
# router touches multiple tables with different per-table behaviour.

from datetime import date

import pytest
from pydantic import ValidationError
from fastapi import HTTPException

import app.routers.schedules as sched_mod
from app.routers.schedules import (
    list_schedules, create_schedule, update_schedule, delete_schedule, schedule_runs,
    ScheduleCreate, ScheduleUpdate,
)


# ─── ScheduleCreate model validators ─────────────────────────────────────────────

def test_schedule_create_rejects_invalid_priority():
    with pytest.raises(ValidationError):
        ScheduleCreate(name="X", recurrence_type="daily", priority="urgentish")


def test_schedule_create_rejects_invalid_recurrence_type():
    with pytest.raises(ValidationError):
        ScheduleCreate(name="X", recurrence_type="hourly")


def test_schedule_create_accepts_explicit_valid_priority():
    # Field validators only run when the value is actually supplied (pydantic v2
    # doesn't validate silently-applied defaults), so this needs an explicit value
    # to exercise the validator's pass-through return at all.
    s = ScheduleCreate(name="X", recurrence_type="daily", priority="urgent")
    assert s.priority == "urgent"


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state, config):
        self.table_name = table_name
        self.state = state
        self.config = config.get(table_name, {})
        self._filters = []
        self._op = "select"
        self._payload = None

    def select(self, *a, **k): return self
    def eq(self, col, val): self._filters.append((col, val)); return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def insert(self, data): self._op = "insert"; self._payload = data; return self
    def update(self, data): self._op = "update"; self._payload = data; return self
    def delete(self): self._op = "delete"; return self

    def execute(self):
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        raises_key = f"{self._op}_raises"
        if raises_key in self.config:
            raise self.config[raises_key]

        key = f"{self._op}_return"
        if key in self.config:
            val = self.config[key]
            return _Resp(val(self._payload, self._filters) if callable(val) else val)

        if self._op == "insert":
            return _Resp([{"id": 1, **(self._payload or {})}])
        if self._op == "update":
            return _Resp([dict(self._payload)] if self._payload else [])
        return _Resp([])


class _FakeSupabase:
    def __init__(self, state, config):
        self.state = state
        self.config = config

    def table(self, name):
        return _FakeQuery(name, self.state, self.config)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(config: dict):
        state = {"calls": []}
        monkeypatch.setattr(sched_mod, "supabase", _FakeSupabase(state, config))
        return state
    return _patch


def _calls(state, table, op=None):
    return [c for c in state["calls"] if c["table"] == table and (op is None or c["op"] == op)]


# ─── list_schedules ──────────────────────────────────────────────────────────────

async def test_list_schedules_happy_path(patch_supabase):
    patch_supabase({"maintenance_schedules": {"select_return": [{"id": 1, "name": "Compressor check"}]}})
    result = await list_schedules(user={"user_id": "u1"})
    assert result == [{"id": 1, "name": "Compressor check"}]


async def test_list_schedules_db_error_is_500(patch_supabase):
    patch_supabase({"maintenance_schedules": {"select_raises": Exception("db down")}})
    with pytest.raises(HTTPException) as exc:
        await list_schedules(user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── create_schedule ─────────────────────────────────────────────────────────────

async def test_create_schedule_computes_next_due_date_when_not_supplied(patch_supabase):
    state = patch_supabase({
        "maintenance_schedules": {"insert_return": lambda payload, f: [dict(payload, id=1)]},
    })
    payload = ScheduleCreate(name="Daily oil check", recurrence_type="daily")
    result = await create_schedule(payload, user={"user_id": "manager-1"})

    insert_payload = _calls(state, "maintenance_schedules", "insert")[0]["payload"]
    assert insert_payload["created_by"] == "manager-1"
    assert insert_payload["next_due_date"] == (date.today() + __import__("datetime").timedelta(days=1)).isoformat()
    assert result["id"] == 1


async def test_create_schedule_preserves_explicit_next_due_date(patch_supabase):
    state = patch_supabase({
        "maintenance_schedules": {"insert_return": lambda payload, f: [dict(payload, id=1)]},
    })
    payload = ScheduleCreate(name="Custom", recurrence_type="daily", next_due_date=date(2030, 1, 1))
    await create_schedule(payload, user={"user_id": "manager-1"})
    insert_payload = _calls(state, "maintenance_schedules", "insert")[0]["payload"]
    assert insert_payload["next_due_date"] == "2030-01-01"


async def test_create_schedule_db_error_is_500(patch_supabase):
    patch_supabase({"maintenance_schedules": {"insert_raises": Exception("db down")}})
    payload = ScheduleCreate(name="Daily oil check", recurrence_type="daily")
    with pytest.raises(HTTPException) as exc:
        await create_schedule(payload, user={"user_id": "manager-1"})
    assert exc.value.status_code == 500


# ─── update_schedule ─────────────────────────────────────────────────────────────

async def test_update_schedule_happy_path(patch_supabase):
    state = patch_supabase({
        "maintenance_schedules": {"update_return": lambda payload, f: [dict(payload, id=1)]},
    })
    result = await update_schedule(1, ScheduleUpdate(name="Renamed"), user={"user_id": "u1"})
    assert result["name"] == "Renamed"
    update_payload = _calls(state, "maintenance_schedules", "update")[0]["payload"]
    assert update_payload == {"name": "Renamed"}


async def test_update_schedule_no_fields_is_400(patch_supabase):
    patch_supabase({})
    with pytest.raises(HTTPException) as exc:
        await update_schedule(1, ScheduleUpdate(), user={"user_id": "u1"})
    assert exc.value.status_code == 400


async def test_update_schedule_not_found_is_404(patch_supabase):
    patch_supabase({"maintenance_schedules": {"update_return": []}})
    with pytest.raises(HTTPException) as exc:
        await update_schedule(999, ScheduleUpdate(name="X"), user={"user_id": "u1"})
    assert exc.value.status_code == 404


async def test_update_schedule_explicit_null_clears_a_field(patch_supabase):
    # schedules.py's own comment: exclude_unset, not a None-filter — an explicit null
    # must reach the DB as a real clear, unlike vfl.py/pto.py's PATCH handlers (see
    # test_vfl_reports.py/test_pto_reports.py — those still filter out `is not None`).
    state = patch_supabase({
        "maintenance_schedules": {"update_return": lambda payload, f: [dict(payload, id=1)]},
    })
    await update_schedule(1, ScheduleUpdate(next_due_date=None), user={"user_id": "u1"})
    update_payload = _calls(state, "maintenance_schedules", "update")[0]["payload"]
    assert "next_due_date" in update_payload
    assert update_payload["next_due_date"] is None


async def test_update_schedule_db_error_is_500(patch_supabase):
    patch_supabase({"maintenance_schedules": {"update_raises": Exception("db down")}})
    with pytest.raises(HTTPException) as exc:
        await update_schedule(1, ScheduleUpdate(name="X"), user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── delete_schedule ─────────────────────────────────────────────────────────────

async def test_delete_schedule_happy_path(patch_supabase):
    state = patch_supabase({})
    result = await delete_schedule(1, user={"user_id": "u1"})
    assert result == {"ok": True}
    assert _calls(state, "maintenance_schedules", "delete")


async def test_delete_schedule_db_error_is_500(patch_supabase):
    patch_supabase({"maintenance_schedules": {"delete_raises": Exception("db down")}})
    with pytest.raises(HTTPException) as exc:
        await delete_schedule(1, user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── schedule_runs ───────────────────────────────────────────────────────────────

async def test_schedule_runs_happy_path(patch_supabase):
    patch_supabase({
        "maintenance_schedule_runs": {"select_return": [{"id": 1, "schedule_id": 1, "due_date": "2026-08-01"}]},
    })
    result = await schedule_runs(1, user={"user_id": "u1"})
    assert result == [{"id": 1, "schedule_id": 1, "due_date": "2026-08-01"}]


async def test_schedule_runs_db_error_is_500(patch_supabase):
    patch_supabase({"maintenance_schedule_runs": {"select_raises": Exception("db down")}})
    with pytest.raises(HTTPException) as exc:
        await schedule_runs(1, user={"user_id": "u1"})
    assert exc.value.status_code == 500
