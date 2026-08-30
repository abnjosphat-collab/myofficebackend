# tests/test_daily_reports_trends.py — get_stats_summary, get_plant_availability_trend,
# and get_equipment_performance_trend all had a live "never fake a 200 on failure"
# violation (ENGINEERING_STANDARDS.md #2): each caught every exception and returned an
# all-zero/empty 200, indistinguishable from "genuinely no reports in range." Same
# documented anti-pattern already fixed once in breakdowns.py's dashboard endpoint and
# issues.py's get_stats (see that file's own comment) — found here as an undocumented
# third instance while writing coverage for this file, and fixed the same way: log and
# re-raise instead of a fake-success return. These tests cover both directions: a real
# DB failure now propagates (doesn't silently look healthy), and genuinely-empty data
# (no exception, just no rows) still correctly returns the zero/empty shape — those are
# two different situations and must stay distinguishable.

import pytest

import app.routers.daily_reports as dr_mod
from app.routers.daily_reports import (
    get_stats_summary, get_plant_availability_trend, get_equipment_performance_trend,
)


class _Resp:
    def __init__(self, data):
        self.data = data


class _FailingQuery:
    """Simulates a real DB failure — .execute() raises."""
    def select(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self): raise Exception("simulated connection failure")


class _WorkingQuery:
    def __init__(self, response_data):
        self._response = response_data

    def select(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, mode, response_data=None):
        self._mode = mode
        self._response_data = response_data

    def table(self, _name):
        if self._mode == "fail":
            return _FailingQuery()
        return _WorkingQuery(self._response_data or [])


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(mode="working", response_data=None):
        monkeypatch.setattr(dr_mod, "supabase", _FakeSupabase(mode, response_data))
    return _patch


# ─── get_stats_summary ───────────────────────────────────────────────────────────────

async def test_stats_summary_db_failure_propagates_instead_of_faking_success(patch_supabase):
    patch_supabase(mode="fail")
    with pytest.raises(Exception, match="simulated connection failure"):
        await get_stats_summary()


async def test_stats_summary_genuinely_no_reports_returns_real_zeros(patch_supabase):
    patch_supabase(mode="working", response_data=[])
    result = await get_stats_summary()
    assert result == {
        "total_reports": 0, "avg_plant_availability": 0,
        "total_callouts": 0, "total_callout_hours": 0, "avg_dam_level": 0,
    }


async def test_stats_summary_computes_averages_and_callout_hours(patch_supabase):
    reports = [
        {"plant_availability_percent": 90, "dam_level": 8, "call_outs": [{"duration_hours": 2}]},
        {"plant_availability_percent": 100, "dam_level": 6, "call_outs": [{"duration_hours": 1.5}, {"duration_hours": 0.5}]},
    ]
    patch_supabase(mode="working", response_data=reports)
    result = await get_stats_summary()
    assert result["total_reports"] == 2
    assert result["avg_plant_availability"] == 95.0
    assert result["avg_dam_level"] == 7.0
    assert result["total_callout_hours"] == 4.0


# ─── get_plant_availability_trend ───────────────────────────────────────────────────

async def test_plant_trend_db_failure_propagates(patch_supabase):
    patch_supabase(mode="fail")
    with pytest.raises(Exception, match="simulated connection failure"):
        await get_plant_availability_trend()


async def test_plant_trend_sorts_ascending_by_date(patch_supabase):
    reports = [
        {"date": "2024-03-01", "plant_availability_percent": 90, "dam_level": 5},
        {"date": "2024-01-01", "plant_availability_percent": 80, "dam_level": 6},
        {"date": "2024-02-01", "plant_availability_percent": 85, "dam_level": 7},
    ]
    patch_supabase(mode="working", response_data=reports)
    result = await get_plant_availability_trend()
    assert result["dates"] == ["2024-01-01", "2024-02-01", "2024-03-01"]
    assert result["plant_availability"] == [80.0, 85.0, 90.0]


# ─── get_equipment_performance_trend ────────────────────────────────────────────────

async def test_equipment_trend_db_failure_propagates(patch_supabase):
    patch_supabase(mode="fail")
    with pytest.raises(Exception, match="simulated connection failure"):
        await get_equipment_performance_trend()


async def test_equipment_trend_groups_by_equipment_name_across_reports(patch_supabase):
    reports = [
        {"date": "2024-01-01", "equipment": [{"name": "Compressor A", "category": "Compressors", "actual": 90}]},
        {"date": "2024-01-02", "equipment": [{"name": "Compressor A", "category": "Compressors", "actual": 95}]},
    ]
    patch_supabase(mode="working", response_data=reports)
    result = await get_equipment_performance_trend()
    assert len(result["equipment_data"]) == 1
    assert result["equipment_data"][0]["name"] == "Compressor A"
    assert result["equipment_data"][0]["performance_data"] == [90.0, 95.0]
    assert result["categories"] == ["Compressors"]


async def test_equipment_trend_skips_entries_with_no_name(patch_supabase):
    reports = [{"date": "2024-01-01", "equipment": [{"category": "Compressors", "actual": 90}]}]
    patch_supabase(mode="working", response_data=reports)
    result = await get_equipment_performance_trend()
    assert result["equipment_data"] == []
