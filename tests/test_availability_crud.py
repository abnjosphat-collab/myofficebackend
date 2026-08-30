# tests/test_availability_crud.py — covers availability.py's handlers that
# test_availability_calcs.py doesn't: get_availabilities (equipment + latest
# availability merge, both the "history exists" and "no history yet" branches),
# get_availability_history, the availability-records CRUD (list/create/update/
# delete), and the try/except -> 500 path shared by every handler in this file.
# Uses the same "call the route coroutine directly against a fake supabase
# client" recipe as test_availability_calcs.py / test_documents_folder_rename.py.

import pytest
from fastapi import HTTPException

import app.routers.availability as availability_mod
from app.routers.availability import (
    AvailRecordIn,
    get_availabilities,
    get_availability_stats,
    get_availability_history,
    list_availability_records,
    availability_from_breakdowns,
    create_availability_record,
    update_availability_record,
    delete_availability_record,
)


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Records every call and resolves a response either from a fixed table
    config or a per-call resolver function, so tests can vary the response
    based on which row/filter a given .execute() call is for (e.g. per-
    equipment "latest availability" lookups in get_availabilities)."""

    def __init__(self, table_name, state, response_map):
        self.table_name = table_name
        self.state = state
        self.response_map = response_map
        self._filters = []
        self._payload = None
        self._op = "select"

    def select(self, *a, **k): return self
    def eq(self, col, val):
        self._filters.append((col, val))
        return self
    def gte(self, col, val):
        self._filters.append((col, val))
        return self
    def lte(self, col, val):
        self._filters.append((col, val))
        return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

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
        cfg = self.response_map.get(self.table_name, {})
        if callable(cfg):
            return _Resp(cfg(self._op, self._filters, self._payload))
        if self._op == "update":
            return _Resp(cfg.get("update_return", []))
        if self._op == "insert":
            return _Resp(cfg.get("insert_return", []))
        if self._op == "delete":
            return _Resp(cfg.get("delete_return", []))
        return _Resp(cfg.get("select_return", []))


class _FakeSupabase:
    def __init__(self, state, response_map):
        self.state = state
        self.response_map = response_map

    def table(self, name):
        return _FakeQuery(name, self.state, self.response_map)


class _RaisingSupabase:
    """Every .table() call raises immediately — used to exercise the generic
    try/except -> HTTPException(500) branch each handler has."""
    def table(self, name):
        raise RuntimeError("db unreachable")


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(response_map: dict):
        state = {"calls": []}
        monkeypatch.setattr(availability_mod, "supabase", _FakeSupabase(state, response_map))
        return state
    return _patch


@pytest.fixture
def patch_supabase_raising(monkeypatch):
    monkeypatch.setattr(availability_mod, "supabase", _RaisingSupabase())


# ─── get_availabilities ──────────────────────────────────────────────────────

async def test_get_availabilities_merges_latest_history_when_present(patch_supabase):
    equipment = [{"id": 1, "name": "Eq1", "status": "operational",
                  "operational_hours": 999, "breakdown_hours": 999}]

    def avail_resolver(op, filters, payload):
        assert ("equipment_id", 1) in filters
        return [{"availability_percentage": 95.5, "operational_hours": 190,
                  "breakdown_hours": 10, "date": "2024-01-10", "status": "operational",
                  "mtbf": 200, "mttr": 2}]

    patch_supabase({"equipment": {"select_return": equipment}, "availabilities": avail_resolver})
    result = await get_availabilities()
    assert len(result) == 1
    eq = result[0]
    assert eq["availability"] == 95.5
    assert eq["operational_hours"] == 190
    assert eq["breakdown_hours"] == 10
    assert eq["uptime"] == 180  # 190 - 10, from the latest record, not the equipment row
    assert eq["downtime"] == 10
    assert eq["mtbf"] == 200
    assert eq["mttr"] == 2
    assert eq["last_maintenance"] == "2024-01-10"


async def test_get_availabilities_falls_back_to_defaults_when_no_history(patch_supabase):
    equipment = [{"id": 2, "name": "Eq2", "operational_hours": 50, "breakdown_hours": 5,
                  "last_maintenance_date": "2023-12-01"}]
    patch_supabase({"equipment": {"select_return": equipment}, "availabilities": {"select_return": []}})
    result = await get_availabilities()
    eq = result[0]
    assert eq["availability"] == 100.0
    assert eq["status"] == "operational"  # default when equipment has no status either
    assert eq["uptime"] == 45  # 50 - 5, from the equipment row itself
    assert eq["mtbf"] == 100
    assert eq["mttr"] == 4
    assert eq["last_maintenance"] == "2023-12-01"


async def test_get_availabilities_raises_500_on_error(patch_supabase_raising):
    with pytest.raises(HTTPException) as exc:
        await get_availabilities()
    assert exc.value.status_code == 500


# ─── exception paths for the two handlers test_availability_calcs.py covers
#     the happy paths of (get_availability_stats / availability_from_breakdowns),
#     which is why those two are the only functions this file gives its own
#     dedicated error-branch test rather than a full behavior suite. ────────

async def test_get_availability_stats_raises_500_on_error(patch_supabase_raising):
    with pytest.raises(HTTPException) as exc:
        await get_availability_stats()
    assert exc.value.status_code == 500


async def test_availability_from_breakdowns_raises_500_on_error(patch_supabase_raising):
    with pytest.raises(HTTPException) as exc:
        await availability_from_breakdowns()
    assert exc.value.status_code == 500


# ─── get_availability_history ────────────────────────────────────────────────

async def test_get_availability_history_returns_rows_for_equipment(patch_supabase):
    state = patch_supabase({"availabilities": {"select_return": [{"id": 1, "equipment_id": 7, "date": "2024-01-01"}]}})
    result = await get_availability_history(7, days=30)
    assert result == [{"id": 1, "equipment_id": 7, "date": "2024-01-01"}]
    call = state["calls"][0]
    assert ("equipment_id", 7) in call["filters"]


async def test_get_availability_history_raises_500_on_error(patch_supabase_raising):
    with pytest.raises(HTTPException) as exc:
        await get_availability_history(1, days=30)
    assert exc.value.status_code == 500


# ─── list_availability_records ───────────────────────────────────────────────

async def test_list_availability_records_applies_filters_and_enriches_names(patch_supabase):
    records = [{"id": 1, "equipment_id": 1, "date": "2024-01-05"}]
    equipment = [{"id": 1, "name": "Compressor A"}]
    state = patch_supabase({
        "availabilities": {"select_return": records},
        "equipment": {"select_return": equipment},
    })
    result = await list_availability_records(equipment_id=1, date_from="2024-01-01", date_to="2024-01-31")
    assert result[0]["equipment_name"] == "Compressor A"

    avail_call = next(c for c in state["calls"] if c["table"] == "availabilities")
    assert ("equipment_id", 1) in avail_call["filters"]
    assert ("date", "2024-01-01") in avail_call["filters"]
    assert ("date", "2024-01-31") in avail_call["filters"]


async def test_list_availability_records_unmatched_equipment_name_is_none(patch_supabase):
    records = [{"id": 1, "equipment_id": 99, "date": "2024-01-05"}]
    patch_supabase({"availabilities": {"select_return": records}, "equipment": {"select_return": []}})
    result = await list_availability_records(equipment_id=None, date_from=None, date_to=None)
    assert result[0]["equipment_name"] is None


async def test_list_availability_records_raises_500_on_error(patch_supabase_raising):
    with pytest.raises(HTTPException) as exc:
        await list_availability_records(equipment_id=None, date_from=None, date_to=None)
    assert exc.value.status_code == 500


# ─── create_availability_record ──────────────────────────────────────────────

def _body(**overrides):
    data = dict(equipment_id=1, date="2024-01-01", operational_hours=10.0,
                breakdown_hours=1.0, availability_percentage=90.0, notes=None)
    data.update(overrides)
    return AvailRecordIn(**data)


async def test_create_availability_record_strips_generated_column_and_returns_row(patch_supabase):
    state = patch_supabase({"availabilities": {"insert_return": [{"id": 5, "equipment_id": 1}]}})
    result = await create_availability_record(_body(), current_user={"user_id": "u1"})
    assert result == {"id": 5, "equipment_id": 1}
    insert_call = state["calls"][0]
    assert insert_call["op"] == "insert"
    # availability_percentage is a DB-generated column — must never be sent on insert
    assert "availability_percentage" not in insert_call["payload"]
    assert "created_at" in insert_call["payload"]
    assert "updated_at" in insert_call["payload"]


async def test_create_availability_record_raises_500_when_insert_returns_nothing(patch_supabase):
    patch_supabase({"availabilities": {"insert_return": []}})
    with pytest.raises(HTTPException) as exc:
        await create_availability_record(_body(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_create_availability_record_raises_500_on_error(patch_supabase_raising):
    with pytest.raises(HTTPException) as exc:
        await create_availability_record(_body(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── update_availability_record ──────────────────────────────────────────────

async def test_update_availability_record_success(patch_supabase):
    state = patch_supabase({"availabilities": {"update_return": [{"id": 5, "notes": "fixed"}]}})
    result = await update_availability_record(5, _body(notes="fixed"), current_user={"user_id": "u1"})
    assert result == {"id": 5, "notes": "fixed"}
    update_call = state["calls"][0]
    assert ("id", 5) in update_call["filters"]
    assert "availability_percentage" not in update_call["payload"]


async def test_update_availability_record_404_when_missing(patch_supabase):
    patch_supabase({"availabilities": {"update_return": []}})
    with pytest.raises(HTTPException) as exc:
        await update_availability_record(999, _body(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 404


async def test_update_availability_record_raises_500_on_error(patch_supabase_raising):
    with pytest.raises(HTTPException) as exc:
        await update_availability_record(1, _body(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── delete_availability_record ──────────────────────────────────────────────

async def test_delete_availability_record_success(patch_supabase):
    state = patch_supabase({"availabilities": {"delete_return": [{"id": 5}]}})
    result = await delete_availability_record(5, current_user={"user_id": "u1", "role": "manager"})
    assert result == {"ok": True}
    delete_call = state["calls"][0]
    assert delete_call["op"] == "delete"
    assert ("id", 5) in delete_call["filters"]


async def test_delete_availability_record_raises_500_on_error(patch_supabase_raising):
    with pytest.raises(HTTPException) as exc:
        await delete_availability_record(1, current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 500
