# tests/test_usage_events.py — app/routers/usage.py: ingest must accept anonymous
# callers (that's the whole point — capturing unsigned-in visitors), attribute a
# user when one is signed in, and drop unknown event types. Pure/mocked; no network.

import pytest

import app.routers.usage as usage_mod
from app.routers.usage import UsageEventBatch, UsageEventIn, ingest_events, list_events


class _FakeTable:
    def __init__(self, state):
        self.state = state
        self._mode = None

    def insert(self, rows):
        self.state["inserted"] = rows
        self._mode = "insert"
        return self

    def select(self, *_a, **_k):
        self._mode = "select"
        return self

    def gte(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        class R:
            def __init__(self, data):
                self.data = data
        if self._mode == "insert":
            return R(self.state["inserted"])
        return R(self.state.get("rows", []))


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        assert name == "usage_events"
        return _FakeTable(self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {"inserted": None, "rows": []}
    monkeypatch.setattr(usage_mod, "supabase", _FakeSupabase(state))
    return state


def _batch(*events):
    return UsageEventBatch(events=list(events))


# ─── ingest_events ──────────────────────────────────────────────────────────────

async def test_ingest_anonymous_visitor_has_no_user_attribution(patch_supabase):
    batch = _batch(UsageEventIn(type="module_open", ts=1_700_000_000_000, session_id="s-1", href="/employees"))
    result = await ingest_events(batch, current_user=None)
    assert result == {"inserted": 1}
    row = patch_supabase["inserted"][0]
    assert row["user_id"] is None
    assert row["user_email"] is None
    assert row["session_id"] == "s-1"
    assert row["href"] == "/employees"


async def test_ingest_signed_in_user_is_attributed(patch_supabase):
    batch = _batch(UsageEventIn(type="page_view", ts=1_700_000_000_000, session_id="s-2", path="/ppe"))
    current_user = {"user_id": "u-1", "email": "a@b.com", "role": "user"}
    result = await ingest_events(batch, current_user=current_user)
    assert result == {"inserted": 1}
    row = patch_supabase["inserted"][0]
    assert row["user_id"] == "u-1"
    assert row["user_email"] == "a@b.com"


async def test_ingest_unknown_event_type_is_dropped(patch_supabase):
    batch = _batch(UsageEventIn(type="not_a_real_type", ts=1_700_000_000_000, session_id="s-3"))
    result = await ingest_events(batch, current_user=None)
    assert result == {"inserted": 0}
    assert patch_supabase["inserted"] is None


async def test_ingest_empty_batch_short_circuits(patch_supabase):
    result = await ingest_events(_batch(), current_user=None)
    assert result == {"inserted": 0}
    assert patch_supabase["inserted"] is None


async def test_ingest_converts_ms_timestamp_to_iso(patch_supabase):
    batch = _batch(UsageEventIn(type="search", ts=1_700_000_000_000, session_id="s-4", query="forklift", results=3))
    await ingest_events(batch, current_user=None)
    row = patch_supabase["inserted"][0]
    assert row["ts"].startswith("2023-11-14")  # 1_700_000_000_000ms == 2023-11-14T22:13:20Z
    assert row["query"] == "forklift"
    assert row["results"] == 3


# ─── list_events ────────────────────────────────────────────────────────────────

async def test_list_events_returns_rows(patch_supabase):
    patch_supabase["rows"] = [{"id": 1, "type": "module_open"}]
    rows = await list_events(since_days=30, limit=100)
    assert rows == [{"id": 1, "type": "module_open"}]


async def test_list_events_clamps_out_of_range_params(patch_supabase):
    # Should not raise even with wildly out-of-range inputs — clamped internally.
    patch_supabase["rows"] = []
    rows = await list_events(since_days=999999, limit=999999999)
    assert rows == []
