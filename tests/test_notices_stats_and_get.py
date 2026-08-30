# tests/test_notices_stats_and_get.py — get_notice (single-item, has no CrudRouter
# generic equivalent) and get_stats (pinned/expired/expiring-soon-within-7-days
# counting, with a malformed-date guard) had zero tests.

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

import app.routers.notices as notices_mod
from app.routers.notices import get_notice, get_stats


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, response_data):
        self._response = response_data

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, records):
        self._records = records

    def table(self, _name):
        return _FakeTable(self._records)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(records):
        monkeypatch.setattr(notices_mod, "supabase", _FakeSupabase(records))
    return _patch


def _iso(days_from_today: int) -> str:
    return (datetime.now() + timedelta(days=days_from_today)).isoformat()


# ─── get_notice ──────────────────────────────────────────────────────────────────────

async def test_get_notice_returns_the_row(patch_supabase):
    patch_supabase([{"id": "n1", "title": "Fire drill"}])
    result = await get_notice("n1")
    assert result["title"] == "Fire drill"


async def test_get_notice_missing_is_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc_info:
        await get_notice("missing")
    assert exc_info.value.status_code == 404


# ─── get_stats ────────────────────────────────────────────────────────────────────────

async def test_stats_empty_is_all_zero(patch_supabase):
    patch_supabase([])
    stats = await get_stats()
    assert stats["total_notices"] == 0
    assert stats["pinned_count"] == 0
    assert stats["expired_count"] == 0


async def test_stats_pinned_count(patch_supabase):
    patch_supabase([{"is_pinned": True}, {"is_pinned": False}, {"is_pinned": True}])
    stats = await get_stats()
    assert stats["pinned_count"] == 2


async def test_stats_expired_vs_expiring_soon_vs_neither(patch_supabase):
    notices = [
        {"expires_at": _iso(-1)},  # already expired
        {"expires_at": _iso(3)},   # expiring within 7 days
        {"expires_at": _iso(30)},  # neither
    ]
    patch_supabase(notices)
    stats = await get_stats()
    assert stats["expired_count"] == 1
    assert stats["expiring_soon_count"] == 1


async def test_stats_malformed_expiry_is_skipped_not_a_crash(patch_supabase):
    patch_supabase([{"expires_at": "not-a-real-date"}])
    stats = await get_stats()
    assert stats["expired_count"] == 0
    assert stats["expiring_soon_count"] == 0


async def test_stats_breakdowns_use_documented_defaults(patch_supabase):
    patch_supabase([{}])  # no status/priority/category at all
    stats = await get_stats()
    assert stats["status_breakdown"] == {"Draft": 1}
    assert stats["priority_breakdown"] == {"Medium": 1}
    assert stats["category_breakdown"] == {"General": 1}
