# tests/test_leaves_calc.py — calculate_total_days (inclusive day counting, a classic
# off-by-one risk for a leave-balance figure) and get_leave_stats' on_leave_now/
# upcoming date-window logic. Both had zero tests despite being real payroll/HR
# calculations.

from datetime import date, timedelta

import pytest

import app.routers.leaves as leaves_mod
from app.leave_days import calculate_total_days
from app.routers.leaves import get_leave_stats


# ─── calculate_total_days ────────────────────────────────────────────────────────────

def test_same_day_leave_is_one_day_not_zero():
    d = date(2024, 3, 15)
    assert calculate_total_days(d, d) == 1


def test_multi_day_leave_is_inclusive_of_both_ends():
    assert calculate_total_days(date(2024, 3, 15), date(2024, 3, 17)) == 3


def test_leave_spanning_a_month_boundary():
    assert calculate_total_days(date(2024, 1, 30), date(2024, 2, 2)) == 4


def test_working_days_exclude_weekend():
    # Fri 2026-09-11 through Mon 2026-09-14 → Fri + Mon = 2
    assert calculate_total_days(date(2026, 9, 11), date(2026, 9, 14), True) == 2


def test_working_days_exclude_public_holiday():
    # Thu–Mon spanning Workers' Day (2026-05-01) → Thu, Fri = 2
    assert calculate_total_days(date(2026, 4, 30), date(2026, 5, 4), True) == 2


# ─── get_leave_stats — on_leave_now / upcoming date windows ────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, response_data):
        self._response = response_data

    def select(self, *a, **k): return self
    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, records):
        self._records = records

    def table(self, _name):
        return _FakeTable(self._records)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(records):
        monkeypatch.setattr(leaves_mod, "supabase", _FakeSupabase(records))
    return _patch


def _iso(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


async def test_currently_active_approved_leave_counts_as_on_leave_now(patch_supabase):
    records = [{"status": "approved", "start_date": _iso(-2), "end_date": _iso(2)}]
    patch_supabase(records)
    stats = await get_leave_stats()
    assert stats["on_leave_now"] == 1
    assert stats["upcoming"] == 0


async def test_future_approved_leave_counts_as_upcoming_not_on_leave_now(patch_supabase):
    records = [{"status": "approved", "start_date": _iso(5), "end_date": _iso(10)}]
    patch_supabase(records)
    stats = await get_leave_stats()
    assert stats["on_leave_now"] == 0
    assert stats["upcoming"] == 1


async def test_pending_leave_in_the_active_window_does_not_count_as_on_leave_now(patch_supabase):
    # Only approved leaves count - a pending request for today's date isn't an
    # actual absence yet.
    records = [{"status": "pending", "start_date": _iso(-1), "end_date": _iso(1)}]
    patch_supabase(records)
    stats = await get_leave_stats()
    assert stats["on_leave_now"] == 0


async def test_past_approved_leave_counts_as_neither(patch_supabase):
    records = [{"status": "approved", "start_date": _iso(-10), "end_date": _iso(-5)}]
    patch_supabase(records)
    stats = await get_leave_stats()
    assert stats["on_leave_now"] == 0
    assert stats["upcoming"] == 0


async def test_status_tallies(patch_supabase):
    records = [
        {"status": "pending", "start_date": "2020-01-01", "end_date": "2020-01-01"},
        {"status": "approved", "start_date": "2020-01-01", "end_date": "2020-01-01"},
        {"status": "approved", "start_date": "2020-01-01", "end_date": "2020-01-01"},
        {"status": "rejected", "start_date": "2020-01-01", "end_date": "2020-01-01"},
    ]
    patch_supabase(records)
    stats = await get_leave_stats()
    assert stats["total"] == 4
    assert stats["pending"] == 1
    assert stats["approved"] == 2
    assert stats["rejected"] == 1
