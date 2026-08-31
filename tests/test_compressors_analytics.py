# tests/test_compressors_analytics.py — get_trend_analysis,
# get_comparison_analytics, get_management_summary. Zero coverage before this
# file.
#
# All three used to catch every exception and return a 200 with a
# `"success": False` body instead of raising/propagating — the exact "never
# fake a 200 on failure" anti-pattern named in ENGINEERING_STANDARDS.md #2,
# and the specific "compressors.py in a few places" instance that doc
# already called out. Fixed 2026-08-31 to re-raise as a real HTTPException
# 500 instead, matching get_performance_metrics below (which never had this
# problem) and every other fix of this pattern elsewhere this session.

import pytest
from fastapi import HTTPException

from app.routers.compressors import (
    get_trend_analysis, get_comparison_analytics, get_management_summary,
    get_performance_metrics, COMPRESSORS_TABLE, READINGS_TABLE,
)
from tests._compressors_fake import FakeSupabase


def _compressor(**overrides):
    base = {"id": "c1", "name": "Compressor A", "location": "Plant 1",
            "status": "running", "total_running_hours": 0.0, "total_loaded_hours": 0.0}
    base.update(overrides)
    return base


def _reading(**overrides):
    base = {"id": "r1", "compressor_id": "c1", "date": "2024-01-01", "efficiency": 80.0,
            "daily_running_hours": 8.0, "daily_loaded_hours": 6.0}
    base.update(overrides)
    return base


# ─── get_performance_metrics: error path (happy paths already covered in
#     tests/test_compressors_performance_metrics.py) ───────────────────────────────────

async def test_performance_metrics_db_failure_raises_500_not_a_fake_200():
    # Unlike the trend/comparison/management-summary endpoints above,
    # get_performance_metrics does NOT catch-and-fake-200 — it raises a real
    # HTTPException. Confirms that's still true and that it's a real 500, not a fake
    # success shape.
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await get_performance_metrics(period_days=30, supabase_client=fake)
    assert exc.value.status_code == 500


# ─── get_trend_analysis ───────────────────────────────────────────────────────────────

async def test_trends_no_readings_at_all_returns_the_no_data_shape():
    fake = FakeSupabase({READINGS_TABLE: [], COMPRESSORS_TABLE: []})
    result = await get_trend_analysis(period="monthly", supabase_client=fake)
    assert result == {
        "success": True, "data": [], "has_data": False,
        "message": "No trend data available yet. Data will appear after daily entries.",
    }


async def test_trends_compressor_with_fewer_than_2_readings_is_excluded():
    fake = FakeSupabase({
        READINGS_TABLE: [_reading(id="r1")],
        COMPRESSORS_TABLE: [_compressor()],
    })
    result = await get_trend_analysis(period="monthly", supabase_client=fake)
    assert result["data"] == []
    assert result["has_data"] is False


async def test_trends_compressor_with_7_to_13_readings_is_silently_dropped():
    # documents actual (surprising) behavior: `older = readings[7:14] if len>=14 else []`
    # means a compressor with, say, 10 readings has readings >= 7 (passes that gate) but
    # `older` comes back empty, so the `if older:` check below fails and NO trend is ever
    # appended for it -- despite having plenty of real data. Not fixed (ambiguous
    # product-intent judgment call, not a narrow obviously-correct bug), just verified.
    readings = [_reading(id=f"r{i}", date=f"2024-01-{i:02d}") for i in range(1, 11)]  # 10 readings
    fake = FakeSupabase({READINGS_TABLE: readings, COMPRESSORS_TABLE: [_compressor()]})
    result = await get_trend_analysis(period="monthly", supabase_client=fake)
    assert result["data"] == []
    assert result["has_data"] is False


async def test_trends_improving_when_recent_efficiency_beats_older_by_more_than_5():
    recent = [_reading(id=f"r{i}", date=f"2024-02-{i:02d}", efficiency=90.0) for i in range(1, 8)]
    older = [_reading(id=f"o{i}", date=f"2024-01-{i:02d}", efficiency=70.0) for i in range(1, 8)]
    fake = FakeSupabase({READINGS_TABLE: recent + older, COMPRESSORS_TABLE: [_compressor()]})
    result = await get_trend_analysis(period="monthly", supabase_client=fake)
    assert result["has_data"] is True
    assert result["data"][0]["efficiency_trend"] == "improving"
    assert result["data"][0]["avg_efficiency"] == 90.0


async def test_trends_declining_when_recent_efficiency_worse_than_older_by_more_than_5():
    recent = [_reading(id=f"r{i}", date=f"2024-02-{i:02d}", efficiency=60.0) for i in range(1, 8)]
    older = [_reading(id=f"o{i}", date=f"2024-01-{i:02d}", efficiency=80.0) for i in range(1, 8)]
    fake = FakeSupabase({READINGS_TABLE: recent + older, COMPRESSORS_TABLE: [_compressor()]})
    result = await get_trend_analysis(period="monthly", supabase_client=fake)
    assert result["data"][0]["efficiency_trend"] == "declining"


async def test_trends_stable_when_within_5_points():
    recent = [_reading(id=f"r{i}", date=f"2024-02-{i:02d}", efficiency=82.0) for i in range(1, 8)]
    older = [_reading(id=f"o{i}", date=f"2024-01-{i:02d}", efficiency=80.0) for i in range(1, 8)]
    fake = FakeSupabase({READINGS_TABLE: recent + older, COMPRESSORS_TABLE: [_compressor()]})
    result = await get_trend_analysis(period="monthly", supabase_client=fake)
    assert result["data"][0]["efficiency_trend"] == "stable"


async def test_trends_db_failure_raises_500_not_a_fake_200():
    fake = FakeSupabase({})
    fake.always_fail(READINGS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await get_trend_analysis(period="monthly", supabase_client=fake)
    assert exc.value.status_code == 500


# ─── get_comparison_analytics ────────────────────────────────────────────────────────

async def test_comparison_efficiency_metric_and_rating_bands():
    fake = FakeSupabase({COMPRESSORS_TABLE: [
        _compressor(id="excellent", total_running_hours=100.0, total_loaded_hours=90.0),  # 90% -> Excellent
        _compressor(id="good", total_running_hours=100.0, total_loaded_hours=65.0),        # 65% -> Good
        _compressor(id="fair", total_running_hours=100.0, total_loaded_hours=45.0),        # 45% -> Fair
        _compressor(id="poor", total_running_hours=100.0, total_loaded_hours=10.0),        # 10% -> Poor
    ]})
    result = await get_comparison_analytics(metric="efficiency", supabase_client=fake)
    ratings = {c["compressor_id"]: c["rating"] for c in result["data"]}
    assert ratings == {"excellent": "Excellent", "good": "Good", "fair": "Fair", "poor": "Poor"}
    # sorted descending by value
    assert [c["compressor_id"] for c in result["data"]] == ["excellent", "good", "fair", "poor"]


async def test_comparison_running_hours_metric_and_rating_bands():
    fake = FakeSupabase({COMPRESSORS_TABLE: [
        _compressor(id="very_high", total_running_hours=2500.0),
        _compressor(id="high", total_running_hours=1500.0),
        _compressor(id="medium", total_running_hours=750.0),
        _compressor(id="low", total_running_hours=100.0),
    ]})
    result = await get_comparison_analytics(metric="running_hours", supabase_client=fake)
    ratings = {c["compressor_id"]: c["rating"] for c in result["data"]}
    assert ratings == {"very_high": "Very High", "high": "High", "medium": "Medium", "low": "Low"}


async def test_comparison_loaded_hours_metric_uses_loaded_value():
    fake = FakeSupabase({COMPRESSORS_TABLE: [
        _compressor(id="c1", total_running_hours=100.0, total_loaded_hours=1200.0),
    ]})
    result = await get_comparison_analytics(metric="loaded_hours", supabase_client=fake)
    assert result["data"][0]["value"] == 1200.0
    assert result["data"][0]["rating"] == "High"


async def test_comparison_unknown_metric_defaults_value_to_zero():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(id="c1", total_running_hours=500.0, total_loaded_hours=400.0)]})
    result = await get_comparison_analytics(metric="bogus", supabase_client=fake)
    assert result["data"][0]["value"] == 0
    assert result["data"][0]["rating"] == "Low"


async def test_comparison_db_failure_raises_500_not_a_fake_200():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await get_comparison_analytics(metric="efficiency", supabase_client=fake)
    assert exc.value.status_code == 500


# ─── get_management_summary ──────────────────────────────────────────────────────────

async def test_management_summary_status_and_location_distribution():
    fake = FakeSupabase({COMPRESSORS_TABLE: [
        _compressor(id="c1", status="running", location="Plant 1"),
        _compressor(id="c2", status="running", location="Plant 2"),
        _compressor(id="c3", status="offline", location="Plant 1"),
    ]})
    result = await get_management_summary(supabase_client=fake)
    assert result["success"] is True
    assert result["total_compressors"] == 3
    assert result["status_distribution"] == {"running": 2, "offline": 1}
    assert result["location_distribution"] == {"Plant 1": 2, "Plant 2": 1}


async def test_management_summary_empty_fleet():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    result = await get_management_summary(supabase_client=fake)
    assert result["total_compressors"] == 0
    assert result["status_distribution"] == {}


async def test_management_summary_db_failure_raises_500_not_a_fake_200():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await get_management_summary(supabase_client=fake)
    assert exc.value.status_code == 500
