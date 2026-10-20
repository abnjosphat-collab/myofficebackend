# tests/test_issues_update_concurrency.py — _update_issue honors expected_updated_at.
import pytest
from fastapi import HTTPException

import app.routers.issues as issues_mod
from app.routers.issues import StockIssueUpdate, _update_issue


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, state):
        self.state = state
        self._op = "select"
        self._payload = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        self.state["update_payload"] = data
        return self

    def execute(self):
        self.state["calls"].append(self._op)
        return _Resp(self.state["responses"].pop(0))


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, _name):
        return _FakeQuery(self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(before, updated):
        state = {"responses": [[before], [updated]], "calls": [], "update_payload": None}
        monkeypatch.setattr(issues_mod, "supabase", _FakeSupabase(state))
        return state
    return _patch


async def test_stale_expectation_is_409_and_saves_nothing(patch_supabase):
    state = patch_supabase(
        {"id": 1, "recipient_name": "A", "items": [], "updated_at": "2026-10-01T00:00:00+00:00"},
        {"id": 1, "recipient_name": "A", "items": []},
    )
    with pytest.raises(HTTPException) as exc:
        await _update_issue(1, StockIssueUpdate(notes="x", expected_updated_at="2020-01-01T00:00:00+00:00"))
    assert exc.value.status_code == 409
    assert "Someone else changed" in exc.value.detail
    assert state["calls"] == ["select"]


async def test_matching_expectation_saves_and_restamps(patch_supabase):
    state = patch_supabase(
        {"id": 1, "recipient_name": "A", "items": [], "updated_at": "2026-10-01T00:00:00+00:00"},
        {"id": 1, "recipient_name": "A", "items": []},
    )
    await _update_issue(1, StockIssueUpdate(notes="x", expected_updated_at="2026-10-01T00:00:00+00:00"))
    assert "expected_updated_at" not in state["update_payload"]
    assert state["update_payload"]["updated_at"] != "2026-10-01T00:00:00+00:00"


async def test_missing_column_stays_silent_until_the_migration(patch_supabase):
    # stock_issues has no updated_at yet (supabase_migration_updated_at_guards.sql,
    # written, not run): the guard must no-op, not 409, until the column exists.
    state = patch_supabase({"id": 1, "recipient_name": "A", "items": []}, {"id": 1, "recipient_name": "A", "items": []})
    await _update_issue(1, StockIssueUpdate(notes="x", expected_updated_at="2020-01-01T00:00:00+00:00"))
    assert state["calls"] == ["select", "update"]
    assert "updated_at" not in state["update_payload"]
