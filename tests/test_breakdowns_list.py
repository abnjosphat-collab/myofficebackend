# tests/test_breakdowns_list.py — GET /api/breakdowns/get-breakdowns: the
# status/breakdown_type/department filters (including the "all" sentinel that must
# NOT translate into an .eq() call), limit/offset pagination via .range(), the
# spares_used JSON-decode on every returned record, the manual build_key()-based
# response cache, and the check_supabase()/exception 500 paths. Zero prior tests —
# test_breakdowns_heatmap.py and test_breakdowns_time_metrics.py don't touch this
# endpoint. Uses the sanctioned "call the route coroutine directly against a fake
# supabase client" recipe (fake defined in tests/_breakdowns_fake.py).

import pytest
from fastapi import HTTPException

import app.routers.breakdowns as bd
from app.routers.breakdowns import get_breakdowns
from tests._breakdowns_fake import FakeSupabase


# ─── Broken-redis fixture: keeps the manual cache_get/cache_set/invalidate_namespace
# calls fast + deterministic without a real Redis (same pattern as
# test_maintenance_work_orders.py). ────────────────────────────────────────────────

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


RECORDS = [
    {"id": "1", "status": "logged", "breakdown_type": "Mechanical", "department": "Milling",
     "created_at": "2026-08-01T00:00:00", "spares_used": '[{"name": "Bearing"}]'},
    {"id": "2", "status": "resolved", "breakdown_type": "Electrical", "department": "Milling",
     "created_at": "2026-08-02T00:00:00", "spares_used": '[]'},
    {"id": "3", "status": "logged", "breakdown_type": "Mechanical", "department": "Crushing",
     "created_at": "2026-08-03T00:00:00", "spares_used": None},
]


def _patch(monkeypatch, records=RECORDS):
    fake = FakeSupabase({"breakdowns": records})
    monkeypatch.setattr(bd, "supabase", fake)
    return fake


async def test_no_filters_returns_all_records_newest_first(monkeypatch):
    fake = _patch(monkeypatch)
    result = await get_breakdowns(status=None, breakdown_type=None, department=None, limit=100, offset=0)
    assert result["success"] is True
    assert result["count"] == 3
    assert [r["id"] for r in result["data"]] == ["3", "2", "1"]  # order("created_at", desc=True)


async def test_spares_used_is_decoded_from_json_string(monkeypatch):
    _patch(monkeypatch)
    result = await get_breakdowns(status=None, breakdown_type=None, department=None, limit=100, offset=0)
    by_id = {r["id"]: r for r in result["data"]}
    assert by_id["1"]["spares_used"] == [{"name": "Bearing"}]
    assert by_id["2"]["spares_used"] == []
    # decode_json_fields only touches string values; a raw None column is left as-is
    # (see its own docstring: "already lists/dicts... left as-is"), not coerced to [].
    assert by_id["3"]["spares_used"] is None


async def test_status_filter_applies_eq(monkeypatch):
    fake = _patch(monkeypatch)
    result = await get_breakdowns(status="logged", breakdown_type=None, department=None, limit=100, offset=0)
    assert result["count"] == 2
    assert all(r["status"] == "logged" for r in result["data"])
    select_calls = [c for c in fake.state.calls if c["table"] == "breakdowns"]
    assert select_calls[0]["eq"] == {"status": "logged"}


async def test_status_all_sentinel_does_not_filter(monkeypatch):
    fake = _patch(monkeypatch)
    result = await get_breakdowns(status="all", breakdown_type=None, department=None, limit=100, offset=0)
    assert result["count"] == 3
    select_calls = [c for c in fake.state.calls if c["table"] == "breakdowns"]
    assert "status" not in select_calls[0]["eq"]


async def test_breakdown_type_and_department_filters_combine(monkeypatch):
    _patch(monkeypatch)
    result = await get_breakdowns(
        status=None, breakdown_type="Mechanical", department="Milling", limit=100, offset=0,
    )
    assert result["count"] == 1
    assert result["data"][0]["id"] == "1"


async def test_pagination_uses_range(monkeypatch):
    fake = _patch(monkeypatch)
    await get_breakdowns(status=None, breakdown_type=None, department=None, limit=1, offset=1)
    select_calls = [c for c in fake.state.calls if c["table"] == "breakdowns"]
    assert select_calls[0]["range"] == (1, 1)  # offset=1, offset+limit-1=1


async def test_no_supabase_client_raises_500(monkeypatch):
    monkeypatch.setattr(bd, "supabase", None)
    with pytest.raises(HTTPException) as exc:
        await get_breakdowns(status=None, breakdown_type=None, department=None, limit=100, offset=0)
    assert exc.value.status_code == 500
    assert "not available" in exc.value.detail


async def test_query_failure_raises_500(monkeypatch):
    fake = FakeSupabase({"breakdowns": RECORDS})
    fake.always_fail("breakdowns", "db exploded")
    monkeypatch.setattr(bd, "supabase", fake)
    with pytest.raises(HTTPException) as exc:
        await get_breakdowns(status=None, breakdown_type=None, department=None, limit=100, offset=0)
    assert exc.value.status_code == 500
    assert "db exploded" in exc.value.detail


# ─── Response cache ─────────────────────────────────────────────────────────────────

class _WorkingRedis:
    def __init__(self):
        self.store = {}
    async def get(self, key): return self.store.get(key)
    async def set(self, key, value, ex=None): self.store[key] = value
    async def sadd(self, key, *values): pass
    async def smembers(self, key): return set()
    async def delete(self, *keys): pass


async def test_second_call_with_same_params_is_served_from_cache(monkeypatch):
    from app import cache as cache_mod
    monkeypatch.setattr(cache_mod, "redis_client", _WorkingRedis())
    monkeypatch.setattr(cache_mod, "_redis_down_until", 0.0)
    fake = _patch(monkeypatch)

    first = await get_breakdowns(status=None, breakdown_type=None, department=None, limit=100, offset=0)
    calls_after_first = len([c for c in fake.state.calls if c["table"] == "breakdowns"])
    second = await get_breakdowns(status=None, breakdown_type=None, department=None, limit=100, offset=0)
    assert second == first
    assert len([c for c in fake.state.calls if c["table"] == "breakdowns"]) == calls_after_first


async def test_different_filters_are_not_served_from_the_same_cache_entry(monkeypatch):
    from app import cache as cache_mod
    monkeypatch.setattr(cache_mod, "redis_client", _WorkingRedis())
    monkeypatch.setattr(cache_mod, "_redis_down_until", 0.0)
    fake = _patch(monkeypatch)

    await get_breakdowns(status=None, breakdown_type=None, department=None, limit=100, offset=0)
    calls_after_first = len([c for c in fake.state.calls if c["table"] == "breakdowns"])
    await get_breakdowns(status="logged", breakdown_type=None, department=None, limit=100, offset=0)
    assert len([c for c in fake.state.calls if c["table"] == "breakdowns"]) == calls_after_first + 1
