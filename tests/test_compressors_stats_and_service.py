# tests/test_compressors_stats_and_service.py — get_stats and get_service_due.
# Zero coverage before this file despite driving the fleet-wide dashboard
# numbers (total hours, average efficiency, upcoming-service count) and the
# sorted service-due worklist.

import pytest
from fastapi import HTTPException

from app.routers.compressors import get_stats, get_service_due, COMPRESSORS_TABLE
from tests._compressors_fake import FakeSupabase


def _compressor(**overrides):
    base = {
        "id": "c1", "name": "Compressor A", "status": "running",
        "total_running_hours": 0.0, "total_loaded_hours": 0.0,
    }
    base.update(overrides)
    return base


# ─── get_stats ────────────────────────────────────────────────────────────────────────

async def test_stats_no_compressors_returns_the_zero_shape():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    result = await get_stats(supabase_client=fake)
    assert result == {
        "total_compressors": 0, "total_running_hours": 0.0, "total_loaded_hours": 0.0,
        "avg_efficiency": 0.0, "active_compressors": 0, "upcoming_services": 0, "urgent_alerts": 0,
    }


async def test_stats_computes_totals_and_active_count():
    fake = FakeSupabase({COMPRESSORS_TABLE: [
        _compressor(id="c1", status="running", total_running_hours=100.0, total_loaded_hours=80.0),
        _compressor(id="c2", status="standby", total_running_hours=50.0, total_loaded_hours=40.0),
        _compressor(id="c3", status="maintenance", total_running_hours=0.0, total_loaded_hours=0.0),
    ]})
    result = await get_stats(supabase_client=fake)
    assert result["total_compressors"] == 3
    assert result["total_running_hours"] == 150.0
    assert result["total_loaded_hours"] == 120.0
    assert result["active_compressors"] == 2  # running + standby, not maintenance
    # avg of (80/100*100=80) and (40/50*100=80) -- the zero-hours compressor is excluded
    assert result["avg_efficiency"] == 80.0


async def test_stats_upcoming_services_counts_compressors_within_30_days():
    fake = FakeSupabase({COMPRESSORS_TABLE: [
        _compressor(id="c1", total_running_hours=900.0),   # 100h to next interval (1000) -> due soon
        _compressor(id="c2", total_running_hours=100.0),   # 900h to next interval -> not due soon
    ]})
    result = await get_stats(supabase_client=fake)
    assert result["upcoming_services"] == 1


async def test_stats_db_failure_is_500():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await get_stats(supabase_client=fake)
    assert exc.value.status_code == 500


# ─── get_service_due ─────────────────────────────────────────────────────────────────

async def test_service_due_sorts_by_urgency_highest_first():
    # generate_service_intervals only ever returns intervals strictly greater than the
    # current hours, so hours_remaining is always > 0 here -- "critical" (days_remaining
    # <= 0) is structurally unreachable through this endpoint; the top real bracket is
    # "high".
    fake = FakeSupabase({COMPRESSORS_TABLE: [
        _compressor(id="low", name="Low", total_running_hours=100.0),     # 900h left -> low
        _compressor(id="high", name="High", total_running_hours=950.0),   # 50h left -> high
        _compressor(id="medium", name="Medium", total_running_hours=900.0),  # 100h left -> medium
    ]})
    result = await get_service_due(supabase_client=fake)
    assert [c["compressor_id"] for c in result] == ["high", "medium", "low"]


async def test_service_due_skips_compressor_past_every_interval():
    fake = FakeSupabase({COMPRESSORS_TABLE: [
        _compressor(id="c1", total_running_hours=20000.0),  # past the last (16000) interval
    ]})
    result = await get_service_due(supabase_client=fake)
    assert result == []


async def test_service_due_computed_fields_are_correct():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(id="c1", name="Compressor A", total_running_hours=950.0)]})
    result = await get_service_due(supabase_client=fake)
    assert len(result) == 1
    entry = result[0]
    assert entry["current_hours"] == 950.0
    assert entry["next_service_hours"] == 1000
    assert entry["hours_remaining"] == 50.0
    assert entry["days_remaining"] == 6  # int(50/8)
    assert entry["urgency"] == "high"


async def test_service_due_db_failure_is_500():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await get_service_due(supabase_client=fake)
    assert exc.value.status_code == 500
