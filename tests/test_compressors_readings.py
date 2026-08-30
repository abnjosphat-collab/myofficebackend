# tests/test_compressors_readings.py — get_compressor_readings,
# get_detailed_readings, get_readings_by_date. All zero coverage before this
# file; get_detailed_readings in particular computes running cumulative
# totals and an overall efficiency figure that had never been exercised.

import pytest
from fastapi import HTTPException

from app.routers.compressors import (
    get_compressor_readings, get_detailed_readings, get_readings_by_date,
    COMPRESSORS_TABLE, READINGS_TABLE,
)
from tests._compressors_fake import FakeSupabase


def _reading(**overrides):
    base = {
        "id": "r1", "compressor_id": "c1", "date": "2024-01-15",
        "daily_running_hours": 8.0, "daily_loaded_hours": 6.0, "efficiency": 75.0,
    }
    base.update(overrides)
    return base


# ─── get_compressor_readings ─────────────────────────────────────────────────────────

async def test_readings_happy_path_returns_success_data_count():
    fake = FakeSupabase({READINGS_TABLE: [_reading(id="r1"), _reading(id="r2", date="2024-01-16")]})
    result = await get_compressor_readings(compressor_id="c1", start_date=None, end_date=None, supabase_client=fake)
    assert result["success"] is True
    assert result["count"] == 2
    assert len(result["data"]) == 2


async def test_readings_empty_is_still_a_200_with_empty_data():
    fake = FakeSupabase({READINGS_TABLE: []})
    result = await get_compressor_readings(compressor_id="c1", start_date=None, end_date=None, supabase_client=fake)
    assert result == {"success": True, "data": [], "count": 0}


async def test_readings_date_range_filters_via_gte_lte():
    fake = FakeSupabase({READINGS_TABLE: [
        _reading(id="r1", date="2024-01-10"),
        _reading(id="r2", date="2024-01-15"),
        _reading(id="r3", date="2024-01-20"),
    ]})
    result = await get_compressor_readings(compressor_id="c1", start_date="2024-01-12", end_date="2024-01-18", supabase_client=fake)
    assert [r["id"] for r in result["data"]] == ["r2"]
    assert result["count"] == 1


async def test_readings_ignores_other_compressors():
    fake = FakeSupabase({READINGS_TABLE: [
        _reading(id="r1", compressor_id="c1"),
        _reading(id="r2", compressor_id="c2"),
    ]})
    result = await get_compressor_readings(compressor_id="c1", start_date=None, end_date=None, supabase_client=fake)
    assert [r["id"] for r in result["data"]] == ["r1"]


async def test_readings_db_failure_is_500_not_a_fake_200():
    fake = FakeSupabase({})
    fake.always_fail(READINGS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await get_compressor_readings(compressor_id="c1", start_date=None, end_date=None, supabase_client=fake)
    assert exc.value.status_code == 500


# ─── get_detailed_readings ───────────────────────────────────────────────────────────

async def test_detailed_readings_no_data_message_shape():
    fake = FakeSupabase({READINGS_TABLE: [], COMPRESSORS_TABLE: []})
    result = await get_detailed_readings(compressor_id="c1", start_date=None, end_date=None, supabase_client=fake)
    assert result == {"success": True, "data": [], "message": "No readings found"}


async def test_detailed_readings_computes_cumulative_totals_from_initial_hours():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [{"id": "c1", "initial_total_running": 100.0, "initial_total_loaded": 80.0}],
        READINGS_TABLE: [
            _reading(id="r1", date="2024-01-15", daily_running_hours=8.0, daily_loaded_hours=6.0),
            _reading(id="r2", date="2024-01-16", daily_running_hours=8.0, daily_loaded_hours=4.0),
        ],
    })
    result = await get_detailed_readings(compressor_id="c1", start_date=None, end_date=None, supabase_client=fake)
    assert result["success"] is True
    assert result["initial_running_hours"] == 100.0
    assert result["initial_loaded_hours"] == 80.0
    assert result["total_running_hours"] == 16.0
    assert result["total_loaded_hours"] == 10.0
    data = result["data"]
    assert data[0]["cumulative_running"] == 108.0
    assert data[0]["cumulative_loaded"] == 86.0
    assert data[1]["cumulative_running"] == 116.0
    assert data[1]["cumulative_loaded"] == 90.0
    # overall efficiency computed from the accumulated running/loaded totals
    assert result["overall_efficiency"] == round((10.0 / 16.0) * 100, 1)


async def test_detailed_readings_missing_compressor_row_defaults_initial_to_zero():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [],  # compressor lookup finds nothing
        READINGS_TABLE: [_reading(id="r1", daily_running_hours=5.0, daily_loaded_hours=5.0)],
    })
    result = await get_detailed_readings(compressor_id="c1", start_date=None, end_date=None, supabase_client=fake)
    assert result["initial_running_hours"] == 0.0
    assert result["initial_loaded_hours"] == 0.0
    assert result["data"][0]["cumulative_running"] == 5.0


async def test_detailed_readings_zero_total_running_hours_avoids_divide_by_zero():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [{"id": "c1", "initial_total_running": 0.0, "initial_total_loaded": 0.0}],
        READINGS_TABLE: [_reading(id="r1", daily_running_hours=0.0, daily_loaded_hours=0.0)],
    })
    result = await get_detailed_readings(compressor_id="c1", start_date=None, end_date=None, supabase_client=fake)
    assert result["overall_efficiency"] == 0


async def test_detailed_readings_db_failure_is_500():
    fake = FakeSupabase({})
    fake.always_fail(READINGS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await get_detailed_readings(compressor_id="c1", start_date=None, end_date=None, supabase_client=fake)
    assert exc.value.status_code == 500


# ─── get_readings_by_date ────────────────────────────────────────────────────────────

async def test_readings_by_date_invalid_format_is_400():
    fake = FakeSupabase({})
    with pytest.raises(HTTPException) as exc:
        await get_readings_by_date(date="15-01-2024", supabase_client=fake)
    assert exc.value.status_code == 400


async def test_readings_by_date_happy_path():
    fake = FakeSupabase({READINGS_TABLE: [
        _reading(id="r1", compressor_id="c1", date="2024-01-15"),
        _reading(id="r2", compressor_id="c2", date="2024-01-15"),
        _reading(id="r3", compressor_id="c1", date="2024-01-16"),
    ]})
    result = await get_readings_by_date(date="2024-01-15", supabase_client=fake)
    assert result["count"] == 2
    assert {r["id"] for r in result["data"]} == {"r1", "r2"}


async def test_readings_by_date_no_matches_is_empty_not_404():
    fake = FakeSupabase({READINGS_TABLE: []})
    result = await get_readings_by_date(date="2024-01-15", supabase_client=fake)
    assert result == {"success": True, "data": [], "count": 0}


async def test_readings_by_date_db_failure_is_500():
    fake = FakeSupabase({})
    fake.always_fail(READINGS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await get_readings_by_date(date="2024-01-15", supabase_client=fake)
    assert exc.value.status_code == 500
