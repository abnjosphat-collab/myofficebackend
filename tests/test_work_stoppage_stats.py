# tests/test_work_stoppage_stats.py — get_stats() feeds the Work Stoppage dashboard's
# tallies (by section, by inspector, corrective-action status counts) and had zero
# tests despite real filtering logic: a report's section value outside the three known
# categories is silently dropped from bySection (still counted in the total). Uses the
# sanctioned "call the route coroutine directly against a fake supabase client" recipe.

import pytest

import app.routers.work_stoppage as ws_mod
from app.routers.work_stoppage import get_stats


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
    def _patch(reports=None, actions=None):
        monkeypatch.setattr(ws_mod, "supabase", _FakeSupabase({
            "work_stoppage_reports": reports or [],
            "corrective_actions": actions or [],
        }))
    return _patch


async def test_empty_input_is_all_zero(patch_supabase):
    patch_supabase()
    stats = await get_stats()
    assert stats["total"] == 0
    assert stats["bySection"] == {"Mechanical": 0, "Electrical": 0, "General": 0}
    assert stats["byInspector"] == {}


async def test_section_counts_only_known_sections(patch_supabase):
    reports = [
        {"section": "Mechanical"}, {"section": "Mechanical"},
        {"section": "Electrical"}, {"section": "Unknown Section"},
    ]
    patch_supabase(reports=reports)
    stats = await get_stats()
    assert stats["total"] == 4
    assert stats["bySection"] == {"Mechanical": 2, "Electrical": 1, "General": 0}


async def test_by_inspector_counts_stoppage_by_field(patch_supabase):
    reports = [{"stoppage_by": "J. Moyo"}, {"stoppage_by": "J. Moyo"}, {"stoppage_by": "T. Ncube"}, {}]
    patch_supabase(reports=reports)
    stats = await get_stats()
    assert stats["byInspector"] == {"J. Moyo": 2, "T. Ncube": 1}


async def test_corrective_actions_tallied_by_status(patch_supabase):
    actions = [
        {"status": "Pending"}, {"status": "Pending"},
        {"status": "In Progress"},
        {"status": "Completed"},
        {"status": "Unrecognized"},
    ]
    patch_supabase(actions=actions)
    stats = await get_stats()
    assert stats["pendingActions"] == 2
    assert stats["inProgressActions"] == 1
    assert stats["completedActions"] == 1
