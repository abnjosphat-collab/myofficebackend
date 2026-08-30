# tests/test_timesheets_stats.py — get_timesheet_stats aggregates regular/overtime/
# holiday/nightshift hours, unique employee/day counts, and averages (with div-by-zero
# guards) for the payroll dashboard. Zero prior tests despite being real payroll
# aggregation, not just a UI tally.

import pytest

import app.routers.timesheets as ts_mod
from app.routers.timesheets import get_timesheet_stats


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, response_data):
        self._response = response_data

    def select(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, records):
        self._records = records

    def table(self, _name):
        return _FakeQuery(self._records)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(records):
        monkeypatch.setattr(ts_mod, "supabase", _FakeSupabase(records))
    return _patch


async def test_empty_records_is_all_zero(patch_supabase):
    patch_supabase([])
    stats = await get_timesheet_stats(start_date=None, end_date=None)
    assert stats["total_entries"] == 0
    assert stats["average_hours_per_day"] == 0
    assert stats["average_hours_per_employee"] == 0


async def test_sums_each_hour_type_independently(patch_supabase):
    records = [
        {"employee_id": "E1", "date": "2024-01-01", "regular_hours": 8, "overtime_hours": 2,
         "holiday_overtime_hours": 0, "nightshift_hours": 0, "total_hours": 10},
        {"employee_id": "E2", "date": "2024-01-01", "regular_hours": 8, "overtime_hours": 0,
         "holiday_overtime_hours": 4, "nightshift_hours": 1, "total_hours": 13},
    ]
    patch_supabase(records)
    stats = await get_timesheet_stats(start_date=None, end_date=None)
    assert stats["total_hours"] == {
        "regular": 16, "overtime": 2, "holiday_overtime": 4, "nightshift": 1, "total": 23,
    }


async def test_counts_unique_employees_and_days_not_row_count(patch_supabase):
    records = [
        {"employee_id": "E1", "date": "2024-01-01", "total_hours": 8},
        {"employee_id": "E1", "date": "2024-01-02", "total_hours": 8},  # same employee, different day
        {"employee_id": "E2", "date": "2024-01-01", "total_hours": 8},  # same day, different employee
    ]
    patch_supabase(records)
    stats = await get_timesheet_stats(start_date=None, end_date=None)
    assert stats["total_entries"] == 3
    assert stats["total_employees"] == 2
    assert stats["total_days"] == 2


async def test_averages_divide_by_unique_counts_not_entry_count(patch_supabase):
    records = [
        {"employee_id": "E1", "date": "2024-01-01", "total_hours": 8},
        {"employee_id": "E1", "date": "2024-01-02", "total_hours": 8},
    ]
    patch_supabase(records)
    stats = await get_timesheet_stats(start_date=None, end_date=None)
    # 16 total hours / 2 unique days = 8; / 1 unique employee = 16 (not /2 entries).
    assert stats["average_hours_per_day"] == 8.0
    assert stats["average_hours_per_employee"] == 16.0


async def test_standby_days_only_counts_true_flag(patch_supabase):
    records = [
        {"employee_id": "E1", "date": "2024-01-01", "standby_allowance": True},
        {"employee_id": "E2", "date": "2024-01-01", "standby_allowance": False},
        {"employee_id": "E3", "date": "2024-01-01"},
    ]
    patch_supabase(records)
    stats = await get_timesheet_stats(start_date=None, end_date=None)
    assert stats["standby_days"] == 1


async def test_status_breakdown(patch_supabase):
    records = [{"employee_id": "E1", "date": "2024-01-01", "status": "approved"},
               {"employee_id": "E2", "date": "2024-01-01", "status": "pending"}]
    patch_supabase(records)
    stats = await get_timesheet_stats(start_date=None, end_date=None)
    assert stats["status_breakdown"] == {"approved": 1, "pending": 1}
