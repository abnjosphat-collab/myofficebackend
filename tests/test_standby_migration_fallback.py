# tests/test_standby_migration_fallback.py — _is_missing_column_error and
# update_assignment's retry-without-optional-columns fallback: when a Supabase table
# is missing a column from a migration that hasn't been run yet (PGRST204), the update
# should transparently retry with just the core fields rather than failing the whole
# request. Real deploy-resilience logic with zero prior tests.

import pytest

from app.routers.standby import _is_missing_column_error, _OPTIONAL_COLS
import app.routers.standby as standby_mod
from app.routers.standby import update_assignment, ShiftRosterUpdate


# ─── _is_missing_column_error ───────────────────────────────────────────────────────

def test_recognizes_postgrest_missing_column_code():
    assert _is_missing_column_error(Exception("PGRST204: schema cache error")) is True


def test_recognizes_could_not_find_column_message():
    assert _is_missing_column_error(Exception("Could not find the 'shift_label' column of 'standby'")) is True


def test_unrelated_error_is_not_recognized():
    assert _is_missing_column_error(Exception("connection timed out")) is False


# ─── update_assignment — retry-without-optional-columns fallback ──────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, state):
        self.state = state
        self._op = "select"
        self._payload = None

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def execute(self):
        self.state.setdefault("calls", []).append({"op": self._op, "payload": self._payload})
        if self._op == "select":
            return _Resp([{"id": 1}])  # _require_exists' existence check
        if self.state.get("raise_on_optional_cols") and self._payload and any(k in self._payload for k in _OPTIONAL_COLS):
            raise Exception("PGRST204: Could not find the 'shift_label' column")
        return _Resp([{"id": 1, **(self._payload or {})}])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, _name):
        return _FakeQuery(self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(raise_on_optional_cols=False):
        state = {"raise_on_optional_cols": raise_on_optional_cols}
        monkeypatch.setattr(standby_mod, "supabase", _FakeSupabase(state))
        return state
    return _patch


async def test_normal_update_succeeds_without_any_retry(patch_supabase):
    state = patch_supabase(raise_on_optional_cols=False)
    result = await update_assignment(1, ShiftRosterUpdate(notes="Updated"), current_user={"user_id": "u1"})
    assert result["notes"] == "Updated"
    update_calls = [c for c in state["calls"] if c["op"] == "update"]
    assert len(update_calls) == 1  # no retry needed


async def test_missing_optional_column_triggers_a_retry_without_it(patch_supabase):
    state = patch_supabase(raise_on_optional_cols=True)
    result = await update_assignment(
        1, ShiftRosterUpdate(notes="Updated", shift_label="Night A"), current_user={"user_id": "u1"}
    )
    update_calls = [c for c in state["calls"] if c["op"] == "update"]
    assert len(update_calls) == 2  # first attempt failed, retried once
    assert "shift_label" in update_calls[0]["payload"]       # first attempt included it
    assert "shift_label" not in update_calls[1]["payload"]   # retry stripped it
    assert "notes" in update_calls[1]["payload"]              # core field preserved
    assert result["notes"] == "Updated"
