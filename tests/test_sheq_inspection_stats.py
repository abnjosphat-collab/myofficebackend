# tests/test_sheq_inspection_stats.py — get_inspection_stats aggregates inspections and
# findings into the dashboard tallies (open/overdue/critical counts, per-section, per-
# inspector) shown on the SHEQ dashboard. sheq_inspections.py was 29% covered with zero
# tests on this aggregation despite it having real parsing logic (inspectors is a
# comma-separated free-text field, not a list column) and implicit filtering (a section
# value outside "mechanical"/"electrical" is silently dropped from bySection, though
# still counted in the total). Uses the sanctioned "call the route coroutine directly
# against a fake supabase client" recipe.

import pytest

import app.routers.sheq_inspections as sheq_mod
from app.routers.sheq_inspections import get_inspection_stats


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
    def _patch(inspections=None, findings=None):
        monkeypatch.setattr(sheq_mod, "supabase", _FakeSupabase({
            "sheq_inspections": inspections or [],
            "sheq_findings": findings or [],
        }))
    return _patch


async def test_empty_input_is_all_zero(patch_supabase):
    patch_supabase()
    stats = await get_inspection_stats()
    assert stats["total"] == 0
    assert stats["open"] == 0
    assert stats["bySection"] == {"mechanical": 0, "electrical": 0}
    assert stats["byInspector"] == {}


async def test_section_counts_only_known_sections(patch_supabase):
    inspections = [
        {"section": "mechanical", "inspectors": ""},
        {"section": "mechanical", "inspectors": ""},
        {"section": "electrical", "inspectors": ""},
        {"section": "general", "inspectors": ""},  # not mechanical/electrical
    ]
    patch_supabase(inspections=inspections)
    stats = await get_inspection_stats()
    assert stats["total"] == 4  # still counted in the total
    assert stats["bySection"] == {"mechanical": 2, "electrical": 1}


async def test_inspector_names_are_split_on_commas_and_deduplicated_by_count(patch_supabase):
    inspections = [
        {"section": "mechanical", "inspectors": "John Doe, Jane Smith"},
        {"section": "mechanical", "inspectors": "John Doe"},
    ]
    patch_supabase(inspections=inspections)
    stats = await get_inspection_stats()
    assert stats["byInspector"] == {"John Doe": 2, "Jane Smith": 1}


async def test_inspector_parsing_ignores_blank_entries_and_extra_whitespace(patch_supabase):
    inspections = [{"section": "mechanical", "inspectors": " John Doe ,, Jane Smith,"}]
    patch_supabase(inspections=inspections)
    stats = await get_inspection_stats()
    assert stats["byInspector"] == {"John Doe": 1, "Jane Smith": 1}


async def test_inspector_missing_field_does_not_crash(patch_supabase):
    patch_supabase(inspections=[{"section": "mechanical"}])
    stats = await get_inspection_stats()
    assert stats["byInspector"] == {}


async def test_findings_are_tallied_by_status(patch_supabase):
    findings = [
        {"status": "open"}, {"status": "open"},
        {"status": "in-progress"},
        {"status": "closed"},
        {"status": "overdue"},
        {"status": "some-unrecognized-status"},
    ]
    patch_supabase(findings=findings)
    stats = await get_inspection_stats()
    assert stats["open"] == 2
    assert stats["inProgress"] == 1
    assert stats["closed"] == 1
    assert stats["overdue"] == 1


async def test_findings_are_tallied_by_priority(patch_supabase):
    findings = [
        {"priority": "critical"}, {"priority": "critical"},
        {"priority": "high"},
        {"priority": "medium"},
        {"priority": "low"},
    ]
    patch_supabase(findings=findings)
    stats = await get_inspection_stats()
    assert stats["critical"] == 2
    assert stats["high"] == 1
    assert stats["medium"] == 1
    assert stats["low"] == 1


async def test_a_single_finding_can_count_toward_both_status_and_priority(patch_supabase):
    patch_supabase(findings=[{"status": "overdue", "priority": "critical"}])
    stats = await get_inspection_stats()
    assert stats["overdue"] == 1
    assert stats["critical"] == 1
