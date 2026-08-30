# tests/test_pto_stats.py — get_pto_stats() feeds the PTO (Planned Task Observation)
# dashboard, including highRiskCount: a report is flagged high-risk if ANY of its
# risk_assessment.made/identified/effective sub-fields is explicitly "No". Had zero
# tests despite that being real safety-critical logic. Documents an actual, worth-
# knowing edge case rather than "fixing" it (out of scope for a coverage pass): a
# report with NO risk_assessment data at all is NOT flagged high-risk, since the check
# is `== "No"` against each sub-field, not "were these ever actually answered" - an
# empty/missing assessment silently reads as fine.

import pytest

import app.routers.pto as pto_mod
from app.routers.pto import get_pto_stats, map_db_pto_to_camel, map_db_action_to_camel


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
        monkeypatch.setattr(pto_mod, "supabase", _FakeSupabase({
            "pto_reports": reports or [],
            "pto_action_plan": actions or [],
        }))
    return _patch


async def test_empty_input_is_all_zero(patch_supabase):
    patch_supabase()
    stats = await get_pto_stats()
    assert stats["total"] == 0
    assert stats["highRiskCount"] == 0


# ─── highRiskCount — the safety-relevant flag ──────────────────────────────────────

async def test_fully_answered_yes_risk_assessment_is_not_high_risk(patch_supabase):
    report = {"risk_assessment": {"made": "Yes", "identified": "Yes", "effective": "Yes"}}
    patch_supabase(reports=[report])
    stats = await get_pto_stats()
    assert stats["highRiskCount"] == 0


@pytest.mark.parametrize("field", ["made", "identified", "effective"])
async def test_any_single_no_answer_flags_high_risk(patch_supabase, field):
    risk = {"made": "Yes", "identified": "Yes", "effective": "Yes"}
    risk[field] = "No"
    patch_supabase(reports=[{"risk_assessment": risk}])
    stats = await get_pto_stats()
    assert stats["highRiskCount"] == 1


async def test_missing_risk_assessment_entirely_is_not_flagged_high_risk(patch_supabase):
    # Documents the actual (surprising) behavior: an absent assessment isn't the same
    # as an explicit "No" to the check, so it silently reads as not-high-risk.
    patch_supabase(reports=[{}])
    stats = await get_pto_stats()
    assert stats["highRiskCount"] == 0


# ─── Observation-type and status tallies ────────────────────────────────────────────

async def test_observation_type_tally(patch_supabase):
    reports = [{"observation_type": "Initial"}, {"observation_type": "Initial"}, {"observation_type": "Follow up"}]
    patch_supabase(reports=reports)
    stats = await get_pto_stats()
    assert stats["initialObservations"] == 2
    assert stats["followUpObservations"] == 1


async def test_status_lifecycle_tally_and_section_and_observer(patch_supabase):
    reports = [
        {"section": "Mechanical", "observer_name": "J. Moyo", "status": "draft"},
        {"section": "Electrical", "observer_name": "J. Moyo", "status": "submitted"},
        {"section": "Unknown", "observer_name": "T. Ncube", "status": "reviewed"},
    ]
    patch_supabase(reports=reports)
    stats = await get_pto_stats()
    assert stats["total"] == 3
    assert stats["bySection"] == {"Mechanical": 1, "Electrical": 1}
    assert stats["byObserver"] == {"J. Moyo": 2, "T. Ncube": 1}
    assert stats["draftCount"] == 1
    assert stats["submittedCount"] == 1
    assert stats["reviewedCount"] == 1


async def test_action_status_tally(patch_supabase):
    actions = [{"status": "Pending"}, {"status": "In Progress"}, {"status": "In Progress"}, {"status": "Completed"}]
    patch_supabase(actions=actions)
    stats = await get_pto_stats()
    assert stats["totalActions"] == 4
    assert stats["pendingActions"] == 1
    assert stats["inProgressActions"] == 2
    assert stats["completedActions"] == 1


# ─── camelCase mappers — nested default fallbacks ──────────────────────────────────

def test_map_pto_report_supplies_default_reasons_shape_when_missing():
    result = map_db_pto_to_camel({"id": "1"})
    assert result["reasons"] == {
        "monthly": False, "newEmployee": False, "safetyAwareness": False,
        "incidentFollowUp": False, "trainingFollowUp": False, "infrequentTask": False,
    }


def test_map_pto_report_preserves_explicit_reasons():
    explicit = {"monthly": True, "newEmployee": False, "safetyAwareness": False,
                "incidentFollowUp": False, "trainingFollowUp": False, "infrequentTask": False}
    result = map_db_pto_to_camel({"id": "1", "reasons": explicit})
    assert result["reasons"] == explicit


def test_map_pto_action_defaults_status_to_pending():
    assert map_db_action_to_camel({"id": "1"})["status"] == "Pending"
