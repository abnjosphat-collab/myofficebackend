# tests/test_notices_issues_helpers.py — notices.py's _dates_to_iso (date object ->
# ISO string conversion before a CrudRouter write) and issues.py's _clean_issue_write
# (trim/blank-to-None normalization) + get_stats (today/this-week counting window).
# All had zero tests.

from datetime import date

import pytest

from app.routers.notices import _dates_to_iso
from app.routers.issues import _clean_issue_write
import app.routers.issues as issues_mod
from app.routers.issues import get_stats


# ─── notices._dates_to_iso ───────────────────────────────────────────────────────────

def test_dates_to_iso_converts_date_field():
    result = _dates_to_iso({"date": date(2024, 3, 15)})
    assert result["date"] == "2024-03-15"


def test_dates_to_iso_converts_expires_at_field():
    result = _dates_to_iso({"expires_at": date(2024, 6, 1)})
    assert result["expires_at"] == "2024-06-01"


def test_dates_to_iso_leaves_non_date_values_untouched():
    result = _dates_to_iso({"date": "already-a-string", "title": "Notice"})
    assert result["date"] == "already-a-string"
    assert result["title"] == "Notice"


# ─── issues._clean_issue_write ──────────────────────────────────────────────────────

def test_clean_issue_write_trims_recipient_name():
    result = _clean_issue_write({"recipient_name": "  J. Moyo  "})
    assert result["recipient_name"] == "J. Moyo"


def test_clean_issue_write_collapses_blank_optional_fields_to_none():
    result = _clean_issue_write({"recipient_id": "", "issued_by": "", "notes": ""})
    assert result["recipient_id"] is None
    assert result["issued_by"] is None
    assert result["notes"] is None


def test_clean_issue_write_preserves_non_blank_values():
    result = _clean_issue_write({"recipient_id": "EMP001", "notes": "Urgent"})
    assert result["recipient_id"] == "EMP001"
    assert result["notes"] == "Urgent"


# ─── issues.get_stats ────────────────────────────────────────────────────────────────

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
        monkeypatch.setattr(issues_mod, "supabase", _FakeSupabase(records))
    return _patch


async def test_stats_counts_todays_issues(patch_supabase):
    today = date.today().isoformat()
    patch_supabase([
        {"issued_at": f"{today}T09:00:00", "recipient_name": "A"},
        {"issued_at": f"{today}T14:00:00", "recipient_name": "B"},
        {"issued_at": "2020-01-01T09:00:00", "recipient_name": "C"},
    ])
    stats = await get_stats()
    assert stats["total"] == 3
    assert stats["today"] == 2
    assert stats["unique_recipients"] == 3


async def test_stats_empty_records_is_all_zero(patch_supabase):
    patch_supabase([])
    stats = await get_stats()
    assert stats == {"total": 0, "today": 0, "this_week": 0, "unique_recipients": 0}


async def test_stats_deduplicates_recipients(patch_supabase):
    today = date.today().isoformat()
    patch_supabase([
        {"issued_at": f"{today}T09:00:00", "recipient_name": "A"},
        {"issued_at": f"{today}T10:00:00", "recipient_name": "A"},
    ])
    stats = await get_stats()
    assert stats["unique_recipients"] == 1
