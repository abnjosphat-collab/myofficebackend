# tests/test_availability_calcs.py — availability.py has no pure helper functions at
# all (every calculation lives inline inside its async route handlers), so these use
# the sanctioned "call the route coroutine directly against a fake supabase client"
# recipe (see test_requisitions_null_clearing.py / frontend's ENGINEERING_STANDARDS.md
# equivalent) rather than a pure-function unit test. availability.py was 18% covered,
# the single lowest-coverage router in the backend, despite computing the equipment
# availability percentages shown on the Availability dashboard.

import pytest

import app.routers.availability as availability_mod
from app.routers.availability import get_availability_stats, availability_from_breakdowns


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    """Ignores every filter/order/limit call and just returns this table's fixed
    response — availability.py's own filtering (equipment_id/date_from/date_to) is
    applied client-side in some paths and server-side (via the query builder) in
    others; these tests control the fixture data directly rather than re-implementing
    postgrest's filter semantics in the fake."""

    def __init__(self, response_data):
        self._response = response_data

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return _FakeTable(self._tables.get(name, []))


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(tables: dict):
        monkeypatch.setattr(availability_mod, "supabase", _FakeSupabase(tables))
    return _patch


# ─── get_availability_stats ─────────────────────────────────────────────────────────

async def test_stats_empty_equipment_returns_all_zero_shape(patch_supabase):
    patch_supabase({"equipment": [], "breakdowns": []})
    result = await get_availability_stats()
    assert result["totalEquipment"] == 0
    assert result["overallAvailability"] == 0


async def test_stats_computes_overall_availability_and_averages(patch_supabase):
    equipment = [
        {"status": "operational", "operational_hours": 200, "breakdown_hours": 20},
        {"status": "operational", "operational_hours": 200, "breakdown_hours": 20},
        {"status": "maintenance", "operational_hours": 0, "breakdown_hours": 0},
        {"status": "breakdown", "operational_hours": 0, "breakdown_hours": 0},
    ]
    patch_supabase({"equipment": equipment, "breakdowns": []})
    result = await get_availability_stats()
    assert result["totalEquipment"] == 4
    assert result["operational"] == 2
    assert result["inMaintenance"] == 1
    assert result["inBreakdown"] == 1
    # (400 - 40) / 400 * 100 = 90.0
    assert result["overallAvailability"] == 90.0
    assert result["avgUptime"] == 90.0   # (400-40)/4
    assert result["avgDowntime"] == 10.0  # 40/4


async def test_stats_zero_total_operational_hours_avoids_divide_by_zero(patch_supabase):
    equipment = [{"status": "operational", "operational_hours": 0, "breakdown_hours": 0}]
    patch_supabase({"equipment": equipment, "breakdowns": []})
    result = await get_availability_stats()
    assert result["overallAvailability"] == 0


async def test_stats_week_availability_derived_from_breakdowns_table(patch_supabase):
    equipment = [{"status": "operational", "operational_hours": 100, "breakdown_hours": 0}] * 2
    week_breakdowns = [{"downtime_minutes": 120}, {"downtime_minutes": 180}]  # 5 hours total
    patch_supabase({"equipment": equipment, "breakdowns": week_breakdowns})
    result = await get_availability_stats()
    # 2 machines * 24h * 7 days = 336 possible hours; 5 down -> (336-5)/336*100
    assert result["weekAvailability"] == round((336 - 5) / 336 * 100, 2)


# ─── availability_from_breakdowns ───────────────────────────────────────────────────

async def test_from_breakdowns_matches_equipment_by_code(patch_supabase):
    equipment = [{"id": 1, "name": "Compressor A", "equipment_id": "COMP-1", "category": "", "department": ""}]
    breakdowns = [{"machine_id": "COMP-1", "machine_name": "Compressor A", "breakdown_date": "2024-01-15", "downtime_minutes": 120}]
    patch_supabase({"equipment": equipment, "breakdowns": breakdowns})
    records = await availability_from_breakdowns()
    assert len(records) == 1
    assert records[0]["equipment_id"] == 1
    assert records[0]["equipment_name"] == "Compressor A"
    assert records[0]["breakdown_hours"] == 2.0
    assert records[0]["availability_percentage"] == round((24 - 2) / 24 * 100, 2)


async def test_from_breakdowns_falls_back_to_matching_by_name(patch_supabase):
    # machine_id code doesn't match any equipment_id, but machine_name matches a name.
    equipment = [{"id": 2, "name": "Loader B", "equipment_id": "LOAD-2", "category": "", "department": ""}]
    breakdowns = [{"machine_id": "UNKNOWN-CODE", "machine_name": "Loader B", "breakdown_date": "2024-01-15", "downtime_minutes": 60}]
    patch_supabase({"equipment": equipment, "breakdowns": breakdowns})
    records = await availability_from_breakdowns()
    assert records[0]["equipment_id"] == 2


async def test_from_breakdowns_unmatched_machine_falls_back_to_raw_code(patch_supabase):
    breakdowns = [{"machine_id": "GHOST-1", "machine_name": "", "breakdown_date": "2024-01-15", "downtime_minutes": 30}]
    patch_supabase({"equipment": [], "breakdowns": breakdowns})
    records = await availability_from_breakdowns()
    assert records[0]["equipment_id"] == "GHOST-1"
    assert records[0]["equipment_name"] == "GHOST-1"


async def test_from_breakdowns_sums_multiple_entries_same_machine_and_date(patch_supabase):
    breakdowns = [
        {"machine_id": "COMP-1", "machine_name": "Compressor A", "breakdown_date": "2024-01-15", "downtime_minutes": 60},
        {"machine_id": "COMP-1", "machine_name": "Compressor A", "breakdown_date": "2024-01-15", "downtime_minutes": 90},
    ]
    patch_supabase({"equipment": [], "breakdowns": breakdowns})
    records = await availability_from_breakdowns()
    assert len(records) == 1  # grouped into one record for that machine+date
    assert records[0]["breakdown_hours"] == 2.5  # (60+90)/60


async def test_from_breakdowns_downtime_is_capped_at_24_hours(patch_supabase):
    # A data/logging error could sum to more than one day's worth of minutes for a
    # single machine+date — must not report negative availability.
    breakdowns = [{"machine_id": "COMP-1", "machine_name": "Compressor A", "breakdown_date": "2024-01-15", "downtime_minutes": 2000}]
    patch_supabase({"equipment": [], "breakdowns": breakdowns})
    records = await availability_from_breakdowns()
    assert records[0]["breakdown_hours"] == 24.0
    assert records[0]["availability_percentage"] == 0.0


async def test_from_breakdowns_equipment_id_filter_excludes_others(patch_supabase):
    equipment = [
        {"id": 1, "name": "A", "equipment_id": "A-1", "category": "", "department": ""},
        {"id": 2, "name": "B", "equipment_id": "B-1", "category": "", "department": ""},
    ]
    breakdowns = [
        {"machine_id": "A-1", "machine_name": "A", "breakdown_date": "2024-01-15", "downtime_minutes": 60},
        {"machine_id": "B-1", "machine_name": "B", "breakdown_date": "2024-01-15", "downtime_minutes": 60},
    ]
    patch_supabase({"equipment": equipment, "breakdowns": breakdowns})
    records = await availability_from_breakdowns(equipment_id=1)
    assert len(records) == 1
    assert records[0]["equipment_id"] == 1


async def test_from_breakdowns_skips_entries_with_blank_date(patch_supabase):
    breakdowns = [{"machine_id": "COMP-1", "machine_name": "Compressor A", "breakdown_date": "", "downtime_minutes": 60}]
    patch_supabase({"equipment": [], "breakdowns": breakdowns})
    records = await availability_from_breakdowns()
    assert records == []


async def test_from_breakdowns_sorts_by_date_descending(patch_supabase):
    breakdowns = [
        {"machine_id": "COMP-1", "machine_name": "A", "breakdown_date": "2024-01-01", "downtime_minutes": 30},
        {"machine_id": "COMP-1", "machine_name": "A", "breakdown_date": "2024-03-01", "downtime_minutes": 30},
        {"machine_id": "COMP-1", "machine_name": "A", "breakdown_date": "2024-02-01", "downtime_minutes": 30},
    ]
    patch_supabase({"equipment": [], "breakdowns": breakdowns})
    records = await availability_from_breakdowns()
    assert [r["date"] for r in records] == ["2024-03-01", "2024-02-01", "2024-01-01"]
