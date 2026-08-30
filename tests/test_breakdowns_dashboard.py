# tests/test_breakdowns_dashboard.py — GET /api/breakdowns/dashboard/overview: the
# metrics computation (total/open/high/critical/today/week counts, today's downtime
# in minutes+hours) and — the one thing this task explicitly asked to CONFIRM rather
# than touch — that a DB failure still raises a real 500 instead of the fake all-zero
# 200 payload the code comment documents having removed. `datetime.utcnow()` is frozen
# via monkeypatch so today/this-week bucketing is deterministic regardless of the day
# this suite actually runs. Uses the sanctioned "call the route coroutine directly
# against a fake supabase client" recipe (fake defined in tests/_breakdowns_fake.py).

import pytest
from datetime import datetime as real_datetime
from fastapi import HTTPException

import app.routers.breakdowns as bd
from app.routers.breakdowns import get_dashboard_overview
from tests._breakdowns_fake import FakeSupabase

# A fixed Sunday: today != week_start (Monday), so "today" and "this week but not
# today" records are unambiguous.
FIXED_NOW = real_datetime(2026, 8, 30, 15, 0, 0)
TODAY = "2026-08-30"
WEEK_START = "2026-08-24"
LAST_WEEK = "2026-08-20"
THIS_WEEK_NOT_TODAY = "2026-08-25"

RECORDS = [
    {"status": "logged", "priority": "high", "breakdown_date": TODAY, "downtime_minutes": 30},
    {"status": "in_progress", "priority": "critical", "breakdown_date": THIS_WEEK_NOT_TODAY, "downtime_minutes": 15},
    {"status": "resolved", "priority": "medium", "breakdown_date": LAST_WEEK, "downtime_minutes": 100},
    {"status": "logged", "priority": "low", "breakdown_date": TODAY, "downtime_minutes": 45},
]


class _FrozenDatetime(real_datetime):
    @classmethod
    def utcnow(cls):
        return FIXED_NOW


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


@pytest.fixture(autouse=True)
def _frozen_time(monkeypatch):
    monkeypatch.setattr(bd, "datetime", _FrozenDatetime)


async def test_dashboard_computes_all_metrics(monkeypatch):
    monkeypatch.setattr(bd, "supabase", FakeSupabase({"breakdowns": RECORDS}))
    result = await get_dashboard_overview()
    assert result["success"] is True
    metrics = result["metrics"]
    assert metrics["total_breakdowns"] == 4
    assert metrics["open_breakdowns"] == 3        # logged/in_progress: r1, r2, r4
    assert metrics["high_priority"] == 1
    assert metrics["critical_priority"] == 1
    assert metrics["today_breakdowns"] == 2        # r1, r4
    assert metrics["today_downtime_minutes"] == 75  # 30 + 45
    assert metrics["today_downtime_hours"] == 1.25
    assert metrics["week_breakdowns"] == 3          # r1, r2, r4 (r3 is last week)


async def test_dashboard_empty_table_is_all_zero(monkeypatch):
    monkeypatch.setattr(bd, "supabase", FakeSupabase({"breakdowns": []}))
    result = await get_dashboard_overview()
    assert result["metrics"]["total_breakdowns"] == 0
    assert result["metrics"]["today_downtime_hours"] == 0


async def test_dashboard_raises_500_on_db_failure_not_a_fake_200(monkeypatch):
    # Confirms the documented fix (see the code comment above the raise in
    # get_dashboard_overview) is still in place: a DB failure must be a real 500, not
    # an all-zero "success": True payload indistinguishable from a healthy empty plant.
    fake = FakeSupabase({"breakdowns": RECORDS})
    fake.always_fail("breakdowns", "db exploded")
    monkeypatch.setattr(bd, "supabase", fake)
    with pytest.raises(HTTPException) as exc:
        await get_dashboard_overview()
    assert exc.value.status_code == 500
    assert "Failed to load dashboard metrics" in exc.value.detail


async def test_dashboard_no_supabase_client_raises_500(monkeypatch):
    monkeypatch.setattr(bd, "supabase", None)
    with pytest.raises(HTTPException) as exc:
        await get_dashboard_overview()
    assert exc.value.status_code == 500
