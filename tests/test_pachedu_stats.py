# tests/test_pachedu_stats.py — get_pachedu_stats() feeds the Pachedu dashboard,
# including totalImpacts/totalChecklist (summed list LENGTHS across every report, not
# simple tallies) guarded by an isinstance(..., list) check against malformed data. Had
# zero tests; the isinstance guard in particular is exactly the kind of defensive check
# that silently rots without a test forcing the non-list branch to actually run.

import pytest

import app.routers.pachedu as pachedu_mod
from app.routers.pachedu import get_pachedu_stats


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
        monkeypatch.setattr(pachedu_mod, "supabase", _FakeSupabase({"pachedu_reports": reports or []}))
    return _patch


async def test_empty_input_is_all_zero(patch_supabase):
    patch_supabase()
    stats = await get_pachedu_stats()
    assert stats["total"] == 0
    assert stats["totalImpacts"] == 0
    assert stats["totalChecklist"] == 0


async def test_impacts_and_checklist_lengths_are_summed_across_reports(patch_supabase):
    reports = [
        {"impacts": ["a", "b"], "checklist": ["x"]},
        {"impacts": ["c"], "checklist": ["y", "z", "w"]},
    ]
    patch_supabase(reports=reports)
    stats = await get_pachedu_stats()
    assert stats["totalImpacts"] == 3
    assert stats["totalChecklist"] == 4


async def test_malformed_non_list_impacts_does_not_crash_or_count(patch_supabase):
    # A defensive isinstance guard exists for exactly this — malformed data (e.g. a
    # string instead of a list) must be silently skipped, not crash len() or miscount.
    reports = [{"impacts": "not-a-list", "checklist": {"also": "not-a-list"}}]
    patch_supabase(reports=reports)
    stats = await get_pachedu_stats()
    assert stats["totalImpacts"] == 0
    assert stats["totalChecklist"] == 0


async def test_missing_impacts_field_is_zero(patch_supabase):
    patch_supabase(reports=[{}])
    stats = await get_pachedu_stats()
    assert stats["totalImpacts"] == 0
    assert stats["totalChecklist"] == 0


async def test_section_behaviour_department_and_status_tallies(patch_supabase):
    reports = [
        {"section_choice": "Mechanical", "behaviour_type": "Intentional", "dept": "Engineering", "status": "draft"},
        {"section_choice": "Electrical", "behaviour_type": "Unintentional", "dept": "Engineering", "status": "submitted"},
        {"section_choice": "Unknown", "behaviour_type": "Unknown", "dept": "Ops", "status": "reviewed"},
    ]
    patch_supabase(reports=reports)
    stats = await get_pachedu_stats()
    assert stats["total"] == 3
    assert stats["bySection"] == {"Mechanical": 1, "Electrical": 1}
    assert stats["byBehaviour"] == {"Intentional": 1, "Unintentional": 1}
    assert stats["byDept"] == {"Engineering": 2, "Ops": 1}
    assert stats["draftCount"] == 1
    assert stats["submittedCount"] == 1
    assert stats["reviewedCount"] == 1
