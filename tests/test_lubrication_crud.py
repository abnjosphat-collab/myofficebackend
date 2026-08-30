# tests/test_lubrication_crud.py — lubrication.py's CRUD endpoints (schedules +
# the separate lube_records sub-resource) had zero tests beyond the pure _lube_status
# window logic (test_compliance_lubrication_status.py). Covers the auto-refreshed
# status on list (recomputed from next_due_date), the section filter, update's
# exclude_unset + status recompute, delete, records listing/filter, and —
# specifically flagged in this task — create_lube_record's real cross-table side
# effect: inserting a lube_records row must ALSO push last_done_date/last_done_hours
# onto the parent lube_schedules row, verified against BOTH tables.

import pytest

import app.routers.lubrication as lube_mod
from app.routers.lubrication import (
    LubeScheduleCreate, LubeScheduleUpdate, LubeRecordCreate,
    get_schedules, create_schedule, update_schedule, delete_schedule,
    get_lube_records, create_lube_record,
)
from fastapi import HTTPException
from datetime import date, timedelta


def _iso(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state):
        self.table_name = table_name
        self.state = state
        self._filters = []
        self._mode = "select"
        self._payload = None

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, *a, **k):
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
            next_id = self.state.setdefault(f"_next_id_{self.table_name}", [1])
            row["id"] = next_id[0]
            next_id[0] += 1
            table.append(row)
            return _Resp([row])

        matches = [r for r in table if all(r.get(c) == v for c, v in self._filters)]
        if self._mode == "select":
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

    def _patch(schedules=None, records=None):
        state["lube_schedules"] = list(schedules or [])
        state["lube_records"] = list(records or [])
        monkeypatch.setattr(lube_mod, "supabase", _FakeSupabase(state))
        return state

    return _patch


def _user():
    return {"user_id": "u-1", "email": "u@x.com", "role": "user"}


def _manager():
    return {"user_id": "m-1", "email": "m@x.com", "role": "manager"}


# ─── get_schedules ───────────────────────────────────────────────────────────────────

async def test_list_recomputes_status_from_next_due_date(patch_supabase):
    patch_supabase(schedules=[
        {"id": 1, "equipment_name": "Conveyor 1", "next_due_date": _iso(-2), "status": "current"},
    ])
    result = await get_schedules(status=None, section=None)
    assert result[0]["status"] == "overdue"


async def test_list_filters_by_section(patch_supabase):
    patch_supabase(schedules=[
        {"id": 1, "equipment_name": "A", "next_due_date": None, "section": "North"},
        {"id": 2, "equipment_name": "B", "next_due_date": None, "section": "South"},
    ])
    result = await get_schedules(status=None, section="South")
    assert [r["id"] for r in result] == [2]


async def test_list_filters_by_recomputed_status(patch_supabase):
    patch_supabase(schedules=[
        {"id": 1, "equipment_name": "A", "next_due_date": _iso(-1), "status": "current"},   # overdue
        {"id": 2, "equipment_name": "B", "next_due_date": _iso(100), "status": "current"},  # current
    ])
    result = await get_schedules(status="overdue", section=None)
    assert [r["id"] for r in result] == [1]


# ─── create_schedule ─────────────────────────────────────────────────────────────────

async def test_create_schedule_computes_status(patch_supabase):
    patch_supabase()
    data = LubeScheduleCreate(
        equipment_name="Conveyor 1", lube_point="Bearing", lubricant_type="Grease",
        next_due_date=_iso(3),
    )
    result = await create_schedule(data, current_user=_user())
    assert result["status"] == "due_soon"


# ─── update_schedule ─────────────────────────────────────────────────────────────────

async def test_update_schedule_only_sent_fields_and_recomputes_status(patch_supabase):
    state = patch_supabase(schedules=[{"id": 1, "equipment_name": "A", "next_due_date": _iso(100), "status": "current"}])
    result = await update_schedule(1, LubeScheduleUpdate(next_due_date=_iso(-1)), current_user=_user())
    assert result["status"] == "overdue"
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert update_calls[0]["payload"]["status"] == "overdue"


async def test_update_schedule_without_next_due_date_leaves_status_untouched(patch_supabase):
    state = patch_supabase(schedules=[{"id": 1, "equipment_name": "A", "next_due_date": _iso(100), "status": "current"}])
    await update_schedule(1, LubeScheduleUpdate(section="North"), current_user=_user())
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert "status" not in update_calls[0]["payload"]


async def test_update_schedule_not_found_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc:
        await update_schedule(999, LubeScheduleUpdate(section="North"), current_user=_user())
    assert exc.value.status_code == 404


# ─── delete_schedule ─────────────────────────────────────────────────────────────────

async def test_delete_schedule_happy_path(patch_supabase):
    state = patch_supabase(schedules=[{"id": 1, "equipment_name": "A"}])
    result = await delete_schedule(1, current_user=_manager())
    assert result == {"ok": True}
    assert state["lube_schedules"] == []


# ─── get_lube_records ────────────────────────────────────────────────────────────────

async def test_get_records_no_filter_returns_all(patch_supabase):
    patch_supabase(records=[
        {"id": 1, "schedule_id": 10, "equipment_name": "A"},
        {"id": 2, "schedule_id": 20, "equipment_name": "B"},
    ])
    result = await get_lube_records(schedule_id=None)
    assert len(result) == 2


async def test_get_records_filters_by_schedule_id(patch_supabase):
    patch_supabase(records=[
        {"id": 1, "schedule_id": 10, "equipment_name": "A"},
        {"id": 2, "schedule_id": 20, "equipment_name": "B"},
    ])
    result = await get_lube_records(schedule_id=10)
    assert [r["id"] for r in result] == [1]


# ─── create_lube_record — the cross-table side effect on the parent schedule ───────

async def test_create_record_updates_parent_schedule_last_done(patch_supabase):
    state = patch_supabase(schedules=[
        {"id": 10, "equipment_name": "Conveyor 1", "last_done_date": None, "last_done_hours": None},
    ])
    data = LubeRecordCreate(
        schedule_id=10, equipment_name="Conveyor 1", lube_point="Bearing",
        done_date="2026-08-15", done_hours=1200, technician="J. Moyo",
    )
    result = await create_lube_record(data, current_user=_user())

    # The record itself was inserted into lube_records.
    assert result["equipment_name"] == "Conveyor 1"
    record_inserts = [c for c in state["calls"] if c["table"] == "lube_records" and c["mode"] == "insert"]
    assert len(record_inserts) == 1
    assert record_inserts[0]["payload"]["done_date"] == "2026-08-15"

    # AND the parent schedule was updated with the new last_done values.
    schedule_updates = [c for c in state["calls"] if c["table"] == "lube_schedules" and c["mode"] == "update"]
    assert len(schedule_updates) == 1
    assert schedule_updates[0]["filters"] == [("id", 10)]
    assert schedule_updates[0]["payload"] == {"last_done_date": "2026-08-15", "last_done_hours": 1200}
    assert state["lube_schedules"][0]["last_done_date"] == "2026-08-15"
    assert state["lube_schedules"][0]["last_done_hours"] == 1200


async def test_create_record_without_schedule_id_does_not_touch_schedules(patch_supabase):
    state = patch_supabase()
    data = LubeRecordCreate(
        equipment_name="Standalone Pump", lube_point="Shaft", done_date="2026-08-15",
    )
    await create_lube_record(data, current_user=_user())
    schedule_updates = [c for c in state["calls"] if c["table"] == "lube_schedules"]
    assert schedule_updates == []


async def test_create_record_insert_failure_raises_500(monkeypatch):
    class _FailingQuery(_FakeQuery):
        def execute(self):
            if self.table_name == "lube_records" and self._mode == "insert":
                return _Resp([])  # simulate insert returning no data
            return super().execute()

    class _FailingSupabase(_FakeSupabase):
        def table(self, name):
            return _FailingQuery(name, self.state)

    state = {"lube_schedules": [], "lube_records": []}
    monkeypatch.setattr(lube_mod, "supabase", _FailingSupabase(state))
    data = LubeRecordCreate(equipment_name="A", lube_point="B", done_date="2026-08-15")
    with pytest.raises(HTTPException) as exc:
        await create_lube_record(data, current_user=_user())
    assert exc.value.status_code == 500
