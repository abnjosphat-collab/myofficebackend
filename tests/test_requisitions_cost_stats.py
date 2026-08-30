# tests/test_requisitions_cost_stats.py — get_daily_total and get_stats compute real
# money totals (sum of cost_per_unit * quantity across requisition items) with zero
# prior tests despite being cost-calculation logic, not just a dashboard tally.

from datetime import date

import pytest

import app.routers.requisitions as req_mod
from app.routers.requisitions import get_daily_total, get_stats


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, response_data):
        self._response = response_data

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(self._tables.get(name, []))


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(requisitions=None, items=None):
        monkeypatch.setattr(req_mod, "supabase", _FakeSupabase({
            "requisitions": requisitions or [],
            "requisition_items": items or [],
        }))
    return _patch


# ─── get_daily_total ─────────────────────────────────────────────────────────────────

async def test_daily_total_no_requisitions_that_day_is_zero(patch_supabase):
    patch_supabase(requisitions=[])
    result = await get_daily_total(date(2024, 3, 15))
    assert result == {"date": "2024-03-15", "total": 0}


async def test_daily_total_sums_cost_times_quantity_across_items(patch_supabase):
    patch_supabase(
        requisitions=[{"id": 1}],
        items=[{"cost_per_unit": 10.0, "quantity": 3}, {"cost_per_unit": 5.5, "quantity": 2}],
    )
    result = await get_daily_total(date(2024, 3, 15))
    assert result["total"] == 41.0  # (10*3) + (5.5*2)


# ─── get_stats ────────────────────────────────────────────────────────────────────────

async def test_stats_empty_is_all_zero(patch_supabase):
    patch_supabase()
    stats = await get_stats()
    assert stats["total_requisitions"] == 0
    assert stats["total_cost"] == 0
    assert stats["status_breakdown"] == {}


async def test_stats_computes_total_cost_and_rounds_to_2dp(patch_supabase):
    patch_supabase(items=[{"cost_per_unit": 3.333, "quantity": 3}])  # 9.999
    stats = await get_stats()
    assert stats["total_cost"] == 10.0


async def test_stats_breaks_down_by_status_and_section(patch_supabase):
    requisitions = [
        {"id": 1, "status": "pending", "section": "Mechanical"},
        {"id": 2, "status": "pending", "section": "Electrical"},
        {"id": 3, "status": "approved", "section": "Mechanical"},
    ]
    patch_supabase(requisitions=requisitions)
    stats = await get_stats()
    assert stats["total_requisitions"] == 3
    assert stats["status_breakdown"] == {"pending": 2, "approved": 1}
    assert stats["section_breakdown"] == {"Mechanical": 2, "Electrical": 1}
