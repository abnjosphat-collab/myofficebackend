# tests/test_vfl_stats.py — get_vfl_stats() feeds the VFL (Visible Felt Leadership)
# dashboard with 6 independent tallies (section, observer, behaviour, observation type,
# coaching technique, status lifecycle) plus action-item status counts, and had zero
# tests. by_observer uses count_by(records, "observer_name") directly — a past bug here
# passed pre-extracted strings instead of raw records (documented in the source, already
# fixed); these tests exercise the current (correct) call shape. Also covers
# map_db_vfl_to_camel/map_db_action_to_camel's default-value fallbacks (status defaults
# to "draft"/"Pending" when the DB row doesn't have one).

import pytest

import app.routers.vfl as vfl_mod
from app.routers.vfl import get_vfl_stats, map_db_vfl_to_camel, map_db_action_to_camel


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
        monkeypatch.setattr(vfl_mod, "supabase", _FakeSupabase({
            "vfl_reports": reports or [],
            "vfl_action_plan": actions or [],
        }))
    return _patch


async def test_empty_input_is_all_zero(patch_supabase):
    patch_supabase()
    stats = await get_vfl_stats()
    assert stats["total"] == 0
    assert stats["bySection"] == {"Mechanical": 0, "Electrical": 0}
    assert stats["byObserver"] == {}
    assert stats["draftCount"] == 0


async def test_full_mixed_dataset_tallies_every_category_correctly(patch_supabase):
    reports = [
        {
            "section_choice": "Mechanical", "observer_name": "J. Moyo",
            "behaviour_category": "Safe Behaviour", "observation_type": "Safe Condition",
            "coaching_technique": "SBR", "status": "draft",
        },
        {
            "section_choice": "Mechanical", "observer_name": "J. Moyo",
            "behaviour_category": "Unsafe Behaviour", "observation_type": "At Risk Behaviour",
            "coaching_technique": "CC", "status": "submitted",
        },
        {
            "section_choice": "Electrical", "observer_name": "T. Ncube",
            "behaviour_category": "Safe Behaviour", "observation_type": "At Risk Condition",
            "coaching_technique": "SBR", "status": "reviewed",
        },
        {
            "section_choice": "Unrecognized Section", "observer_name": "T. Ncube",
            "behaviour_category": "Unrecognized", "observation_type": "Unrecognized",
            "coaching_technique": "Unrecognized", "status": "closed",
        },
    ]
    actions = [{"status": "Pending"}, {"status": "In Progress"}, {"status": "Completed"}, {"status": "Completed"}]
    patch_supabase(reports=reports, actions=actions)
    stats = await get_vfl_stats()

    assert stats["total"] == 4
    assert stats["bySection"] == {"Mechanical": 2, "Electrical": 1}  # unrecognized dropped, still in total
    assert stats["byObserver"] == {"J. Moyo": 2, "T. Ncube": 2}
    assert stats["byBehaviour"] == {"Safe Behaviour": 2, "Unsafe Behaviour": 1}
    assert stats["byObservationType"] == {
        "Safe Behaviour": 0, "Safe Condition": 1, "At Risk Behaviour": 1, "At Risk Condition": 1,
    }
    assert stats["byCoaching"] == {"SBR": 2, "CC": 1}
    assert stats["draftCount"] == 1
    assert stats["submittedCount"] == 1
    assert stats["reviewedCount"] == 1
    assert stats["closedCount"] == 1

    assert stats["totalActions"] == 4
    assert stats["pendingActions"] == 1
    assert stats["inProgressActions"] == 1
    assert stats["completedActions"] == 2


async def test_report_missing_status_field_defaults_to_draft(patch_supabase):
    patch_supabase(reports=[{"section_choice": "Mechanical"}])  # no "status" key at all
    stats = await get_vfl_stats()
    assert stats["draftCount"] == 1


# ─── camelCase mappers — default-value fallbacks ────────────────────────────────────

def test_map_vfl_report_defaults_status_to_draft_when_missing():
    assert map_db_vfl_to_camel({"id": "1"})["status"] == "draft"


def test_map_vfl_report_preserves_explicit_status():
    assert map_db_vfl_to_camel({"id": "1", "status": "closed"})["status"] == "closed"


def test_map_action_defaults_status_to_pending_when_missing():
    assert map_db_action_to_camel({"id": "1"})["status"] == "Pending"
