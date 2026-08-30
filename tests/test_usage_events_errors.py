# tests/test_usage_events_errors.py — ingest_events/list_events's error paths (a DB
# failure must raise a real 500, not silently swallow) had no coverage;
# test_usage_events.py only covers the success paths.

import pytest

import app.routers.usage as usage_mod
from app.routers.usage import UsageEventBatch, UsageEventIn, ingest_events, list_events


class _FailingTable:
    def insert(self, *a, **k): return self
    def select(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self): raise Exception("simulated DB failure")


class _FailingSupabase:
    def table(self, _name):
        return _FailingTable()


@pytest.fixture
def patch_failing_supabase(monkeypatch):
    monkeypatch.setattr(usage_mod, "supabase", _FailingSupabase())


async def test_ingest_db_failure_raises_500_not_silent(patch_failing_supabase):
    batch = UsageEventBatch(events=[UsageEventIn(type="module_open", ts=1_700_000_000_000, session_id="s-1")])
    with pytest.raises(Exception) as exc_info:
        await ingest_events(batch, current_user=None)
    assert getattr(exc_info.value, "status_code", None) == 500


async def test_list_events_db_failure_raises_500_not_silent(patch_failing_supabase):
    with pytest.raises(Exception) as exc_info:
        await list_events(since_days=30, limit=100)
    assert getattr(exc_info.value, "status_code", None) == 500
