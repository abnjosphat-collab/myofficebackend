# tests/test_ppe_stats.py — get_ppe_stats()' expiry-window logic (expired vs expiring
# within 30 days, only counting active-status records) had zero tests despite being
# safety-critical: PPE past its replacement date is a real compliance risk, and this
# is the number that drives the dashboard's expiry warnings.

from datetime import date, timedelta

import pytest

import app.routers.ppe as ppe_mod
from app.routers.ppe import get_ppe_stats


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
        monkeypatch.setattr(ppe_mod, "supabase", _FakeSupabase(records))
    return _patch


def _iso(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


async def test_empty_records_is_all_zero(patch_supabase):
    patch_supabase([])
    stats = await get_ppe_stats()
    assert stats["total_records"] == 0
    assert stats["expired"] == 0
    assert stats["expiring_soon"] == 0


async def test_expired_record_is_counted(patch_supabase):
    records = [{"id": 1, "employee_id": "E1", "status": "active", "condition": "good", "expiry_date": _iso(-1)}]
    patch_supabase(records)
    stats = await get_ppe_stats()
    assert stats["expired"] == 1
    assert stats["expiring_soon"] == 0


async def test_expiring_within_30_days_is_counted(patch_supabase):
    records = [{"id": 1, "employee_id": "E1", "status": "active", "condition": "good", "expiry_date": _iso(15)}]
    patch_supabase(records)
    stats = await get_ppe_stats()
    assert stats["expiring_soon"] == 1
    assert stats["expired"] == 0


@pytest.mark.parametrize("days_out,expected_bucket", [(30, "expiring_soon"), (31, "neither")])
async def test_30_day_boundary(patch_supabase, days_out, expected_bucket):
    records = [{"id": 1, "employee_id": "E1", "status": "active", "condition": "good", "expiry_date": _iso(days_out)}]
    patch_supabase(records)
    stats = await get_ppe_stats()
    if expected_bucket == "expiring_soon":
        assert stats["expiring_soon"] == 1
        assert stats["expired"] == 0
    else:
        assert stats["expiring_soon"] == 0
        assert stats["expired"] == 0


async def test_expired_item_with_non_active_status_is_not_counted(patch_supabase):
    # A retired/replaced item's old expiry date shouldn't still trigger an expiry
    # warning - only 'active' records are checked.
    records = [{"id": 1, "employee_id": "E1", "status": "replaced", "condition": "worn", "expiry_date": _iso(-100)}]
    patch_supabase(records)
    stats = await get_ppe_stats()
    assert stats["expired"] == 0
    assert stats["expiring_soon"] == 0


async def test_malformed_expiry_date_is_skipped_not_a_crash(patch_supabase):
    records = [{"id": 1, "employee_id": "E1", "status": "active", "condition": "good", "expiry_date": "not-a-date"}]
    patch_supabase(records)
    stats = await get_ppe_stats()
    assert stats["expired"] == 0
    assert stats["expiring_soon"] == 0
    assert stats["total_records"] == 1  # still counted as a record, just not in the expiry buckets


async def test_status_and_condition_breakdowns_and_unique_employees(patch_supabase):
    records = [
        {"id": 1, "employee_id": "E1", "status": "active", "condition": "good", "expiry_date": None},
        {"id": 2, "employee_id": "E1", "status": "active", "condition": "worn", "expiry_date": None},
        {"id": 3, "employee_id": "E2", "status": "replaced", "condition": "good", "expiry_date": None},
    ]
    patch_supabase(records)
    stats = await get_ppe_stats()
    assert stats["total_records"] == 3
    assert stats["unique_employees"] == 2
    assert stats["status_breakdown"] == {"active": 2, "replaced": 1}
    assert stats["condition_breakdown"] == {"good": 2, "worn": 1}
