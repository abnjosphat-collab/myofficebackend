# tests/test_near_miss_stats.py — get_stats() feeds the Near Miss dashboard (by
# section, by reporter) and had zero tests. Same filtering shape as
# sheq_inspections/work_stoppage's equivalents: a section value outside the three known
# categories is silently dropped from bySection while still counting toward the total.

import pytest

import app.routers.near_miss as nm_mod
from app.routers.near_miss import get_stats


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, response_data):
        self._response = response_data

    def select(self, *a, **k): return self
    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return _FakeTable(self._tables.get(name, []))


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(reports=None):
        monkeypatch.setattr(nm_mod, "supabase", _FakeSupabase({"nearmiss_reports": reports or []}))
    return _patch


async def test_empty_input_is_all_zero(patch_supabase):
    patch_supabase()
    stats = await get_stats()
    assert stats["total"] == 0
    assert stats["bySection"] == {"Mechanical": 0, "Electrical": 0, "General": 0}
    assert stats["byReporter"] == {}


async def test_section_counts_only_known_sections(patch_supabase):
    reports = [{"section": "Mechanical"}, {"section": "Electrical"}, {"section": "Electrical"}, {"section": "Other"}]
    patch_supabase(reports=reports)
    stats = await get_stats()
    assert stats["total"] == 4
    assert stats["bySection"] == {"Mechanical": 1, "Electrical": 2, "General": 0}


async def test_by_reporter_counts_and_ignores_blank(patch_supabase):
    reports = [{"reportername": "A. Ncube"}, {"reportername": "A. Ncube"}, {"reportername": ""}, {}]
    patch_supabase(reports=reports)
    stats = await get_stats()
    assert stats["byReporter"] == {"A. Ncube": 2}
