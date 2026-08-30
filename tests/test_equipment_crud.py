# tests/test_equipment_crud.py — the six handlers left uncovered after
# test_equipment_date_conversion.py (process_dates_for_db/process_dates_from_db) and
# generate_equipment_id's own coverage in test_employees_search.py: get_equipment,
# get_equipment_item, create_equipment, update_equipment, delete_equipment,
# equipment_health. Covers display_name's exclusion from the Supabase write payload
# (it's a @computed_field, not a real column — the insert/update would 500 without the
# exclude), auto-generated vs. caller-supplied equipment_id, the "don't blank out
# equipment_id on update" guard, and every handler's generic-exception -> 500 path.
# Uses the sanctioned "call the route coroutine directly against a fake supabase
# client" recipe, with an explicit response queue since create/update/delete each
# issue more than one distinct call against the same table per request.

from datetime import date

import pytest
from fastapi import HTTPException

import app.routers.equipment as eq_mod
from app.routers.equipment import (
    Equipment,
    get_equipment, get_equipment_item,
    create_equipment, update_equipment, delete_equipment,
    equipment_health,
)


# ─── Broken-redis fixture: get_equipment is wrapped in @cached — keep it fast and
# deterministic without a real local Redis. ─────────────────────────────────────────

class _BrokenRedis:
    async def get(self, *a, **k): raise ConnectionError("no redis in tests")
    async def set(self, *a, **k): raise ConnectionError("no redis in tests")
    async def sadd(self, *a, **k): raise ConnectionError("no redis in tests")
    async def smembers(self, *a, **k): raise ConnectionError("no redis in tests")
    async def delete(self, *a, **k): raise ConnectionError("no redis in tests")


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    from app import cache as cache_mod
    monkeypatch.setattr(cache_mod, "redis_client", _BrokenRedis())


# ─── Fake supabase: records every call and serves responses off a queue, one per
# execute() call in the order the handler issues them. ──────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _RaisingResp:
    def __init__(self, exc):
        self.exc = exc


class _FakeQuery:
    def __init__(self, table_name, calls, queue):
        self.table_name = table_name
        self._calls = calls
        self._queue = queue
        self._filters = []
        self._op = "select"
        self._payload = None

    def select(self, *a, **k): return self
    def eq(self, col, val): self._filters.append(("eq", col, val)); return self
    def limit(self, n): return self

    def insert(self, data):
        self._op = "insert"; self._payload = data; return self

    def update(self, data):
        self._op = "update"; self._payload = data; return self

    def delete(self):
        self._op = "delete"; return self

    def execute(self):
        self._calls.append({
            "table": self.table_name, "op": self._op,
            "filters": list(self._filters), "payload": self._payload,
        })
        item = self._queue.pop(0)
        if isinstance(item, _RaisingResp):
            raise item.exc
        return _Resp(item)


class _FakeSupabase:
    def __init__(self, calls, queue):
        self._calls = calls
        self._queue = queue

    def table(self, name):
        return _FakeQuery(name, self._calls, self._queue)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(responses):
        calls = []
        monkeypatch.setattr(eq_mod, "supabase", _FakeSupabase(calls, list(responses)))
        return calls
    return _patch


def _equipment(**overrides):
    base = dict(name="Air Compressor")
    base.update(overrides)
    return Equipment(**base)


# ─── EquipmentBase.display_name / .matches() ────────────────────────────────────────

def test_display_name_combines_id_and_name_when_id_present():
    e = _equipment(equipment_id="EQ-1", name="Air Compressor")
    assert e.display_name == "EQ-1 — Air Compressor"


def test_display_name_is_just_name_when_no_id():
    e = _equipment(name="Air Compressor")
    assert e.display_name == "Air Compressor"


def test_matches_checks_name_id_model_and_manufacturer_case_insensitively():
    e = _equipment(name="Air Compressor", model="AC-500", manufacturer="Atlas Copco")
    assert e.matches("compressor")
    assert e.matches("AC-500")
    assert e.matches("atlas")
    assert not e.matches("nonexistent")


# ─── equipment_health ────────────────────────────────────────────────────────────────

async def test_health_reports_healthy_on_success(patch_supabase):
    patch_supabase([[{"id": 1}]])
    result = await equipment_health()
    assert result["status"] == "healthy"
    assert result["service"] == "equipment"


async def test_health_reports_503_on_db_failure(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("db down"))])
    with pytest.raises(HTTPException) as exc:
        await equipment_health()
    assert exc.value.status_code == 503


# ─── get_equipment ───────────────────────────────────────────────────────────────────

async def test_get_equipment_converts_dates_on_every_row(patch_supabase):
    patch_supabase([[
        {"id": 1, "purchase_date": "2022-06-01"},
        {"id": 2, "warranty_expiry": "2025-12-31"},
    ]])
    result = await get_equipment()
    assert result[0]["purchase_date"] == date(2022, 6, 1)
    assert result[1]["warranty_expiry"] == date(2025, 12, 31)


async def test_get_equipment_empty_rows_is_empty_list(patch_supabase):
    patch_supabase([[]])
    assert await get_equipment() == []


async def test_get_equipment_db_failure_is_500(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("boom"))])
    with pytest.raises(HTTPException) as exc:
        await get_equipment()
    assert exc.value.status_code == 500


# ─── get_equipment_item ──────────────────────────────────────────────────────────────

async def test_get_equipment_item_found_converts_dates(patch_supabase):
    patch_supabase([[{"id": 5, "purchase_date": "2019-11-20"}]])
    result = await get_equipment_item(5)
    assert result["purchase_date"] == date(2019, 11, 20)


async def test_get_equipment_item_not_found_is_404(patch_supabase):
    patch_supabase([[]])
    with pytest.raises(HTTPException) as exc:
        await get_equipment_item(999)
    assert exc.value.status_code == 404


async def test_get_equipment_item_db_failure_is_500(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("boom"))])
    with pytest.raises(HTTPException) as exc:
        await get_equipment_item(5)
    assert exc.value.status_code == 500


# ─── create_equipment ────────────────────────────────────────────────────────────────

async def test_create_equipment_auto_generates_id_when_not_supplied(patch_supabase):
    calls = patch_supabase([
        [{"id": 1, "equipment_id": "EQ-123", "name": "Air Compressor"}],
    ])
    result = await create_equipment(_equipment(), current_user={"user_id": "u1"})
    insert_payload = [c for c in calls if c["op"] == "insert"][0]["payload"]
    assert insert_payload["equipment_id"].startswith("EQ-")
    assert result["name"] == "Air Compressor"


async def test_create_equipment_keeps_caller_supplied_id(patch_supabase):
    calls = patch_supabase([
        [{"id": 1, "equipment_id": "MY-ID", "name": "Air Compressor"}],
    ])
    await create_equipment(_equipment(equipment_id="MY-ID"), current_user={"user_id": "u1"})
    insert_payload = [c for c in calls if c["op"] == "insert"][0]["payload"]
    assert insert_payload["equipment_id"] == "MY-ID"


async def test_create_equipment_excludes_display_name_from_payload(patch_supabase):
    calls = patch_supabase([
        [{"id": 1, "equipment_id": "MY-ID", "name": "Air Compressor"}],
    ])
    await create_equipment(_equipment(equipment_id="MY-ID"), current_user={"user_id": "u1"})
    insert_payload = [c for c in calls if c["op"] == "insert"][0]["payload"]
    assert "display_name" not in insert_payload


async def test_create_equipment_converts_dates_for_db(patch_supabase):
    calls = patch_supabase([
        [{"id": 1, "equipment_id": "EQ-1", "name": "Compressor"}],
    ])
    await create_equipment(
        _equipment(purchase_date=date(2022, 6, 1)), current_user={"user_id": "u1"}
    )
    insert_payload = [c for c in calls if c["op"] == "insert"][0]["payload"]
    assert insert_payload["purchase_date"] == "2022-06-01"


async def test_create_equipment_no_rows_returned_is_500(patch_supabase):
    patch_supabase([[]])
    with pytest.raises(HTTPException) as exc:
        await create_equipment(_equipment(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_create_equipment_db_failure_is_500(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("boom"))])
    with pytest.raises(HTTPException) as exc:
        await create_equipment(_equipment(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── update_equipment ────────────────────────────────────────────────────────────────

async def test_update_equipment_not_found_is_404(patch_supabase):
    patch_supabase([[]])
    with pytest.raises(HTTPException) as exc:
        await update_equipment(999, _equipment(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 404


async def test_update_equipment_success_converts_dates_back(patch_supabase):
    patch_supabase([
        [{"id": 5}],  # existence check
        [{"id": 5, "name": "Air Compressor", "purchase_date": "2022-06-01"}],  # update
    ])
    result = await update_equipment(5, _equipment(), current_user={"user_id": "u1"})
    assert result["purchase_date"] == date(2022, 6, 1)


async def test_update_equipment_blank_equipment_id_is_not_sent(patch_supabase):
    calls = patch_supabase([
        [{"id": 5}],
        [{"id": 5, "name": "Air Compressor"}],
    ])
    await update_equipment(5, _equipment(equipment_id=""), current_user={"user_id": "u1"})
    update_payload = [c for c in calls if c["op"] == "update"][0]["payload"]
    assert "equipment_id" not in update_payload


async def test_update_equipment_no_rows_returned_is_500(patch_supabase):
    patch_supabase([
        [{"id": 5}],
        [],  # update "succeeds" but returns nothing
    ])
    with pytest.raises(HTTPException) as exc:
        await update_equipment(5, _equipment(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_update_equipment_db_failure_is_500(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("boom"))])
    with pytest.raises(HTTPException) as exc:
        await update_equipment(5, _equipment(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── delete_equipment ────────────────────────────────────────────────────────────────

async def test_delete_equipment_not_found_is_404(patch_supabase):
    patch_supabase([[]])
    with pytest.raises(HTTPException) as exc:
        await delete_equipment(999, current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 404


async def test_delete_equipment_success_reports_name_and_id(patch_supabase):
    calls = patch_supabase([
        [{"id": 5, "name": "Air Compressor"}],
        None,
    ])
    result = await delete_equipment(5, current_user={"user_id": "u1", "role": "manager"})
    assert result == {
        "success": True,
        "detail": "Equipment 5 (Air Compressor) successfully deleted",
        "deleted_id": 5,
    }
    assert any(c["op"] == "delete" for c in calls)


async def test_delete_equipment_missing_name_falls_back_to_unknown(patch_supabase):
    patch_supabase([
        [{"id": 5}],
        None,
    ])
    result = await delete_equipment(5, current_user={"user_id": "u1", "role": "manager"})
    assert "Unknown" in result["detail"]


async def test_delete_equipment_db_failure_is_500(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("boom"))])
    with pytest.raises(HTTPException) as exc:
        await delete_equipment(5, current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 500
