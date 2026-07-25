# tests/test_requisitions_null_clearing.py — update_requisition (app/routers/requisitions.py)
# used to build its update payload with a per-field `if update.X is not None` check,
# which is the exact null-filter bug already fixed once elsewhere (edec24a): an
# explicitly-sent null (e.g. clearing `notes`) never reached the update() call because
# it looked identical to "field not sent at all". Fixed to exclude_unset(); this locks
# the correct behaviour in. Pure/mocked; no network.

import pytest

import app.routers.requisitions as requisitions_mod
from app.routers.requisitions import RequisitionUpdate, update_requisition


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    """Tracks the payload passed to update(); select()/eq()/neq() are all no-ops that
    return sensible defaults for update_requisition's own existence/conflict checks and
    its final refetch."""

    def __init__(self, state):
        self.state = state
        self._mode = None

    def select(self, *_a, **_k):
        self._mode = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def neq(self, *_a, **_k):
        return self

    def update(self, data):
        self.state["update_payload"] = data
        self._mode = "update"
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def insert(self, *_a, **_k):
        self._mode = "insert"
        return self

    def execute(self):
        if self._mode == "update":
            return _Resp([{"id": 1, **self.state["update_payload"]}])
        # select() covers: existing-row check, requisition_number conflict check, and
        # the final "return the updated row" refetch — all fine with one fixed row.
        return _Resp([{"id": 1, "requisition_items": []}])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeTable(self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {"update_payload": None}
    monkeypatch.setattr(requisitions_mod, "supabase", _FakeSupabase(state))
    return state


async def test_explicit_null_clears_a_nullable_field(patch_supabase):
    # notes=None, but explicitly SET (not just defaulted) — exclude_unset must still
    # include it, so the clear reaches the database.
    update = RequisitionUpdate(notes=None, priority="high")
    await update_requisition(1, update, current_user={"user_id": "u1"})
    payload = patch_supabase["update_payload"]
    assert "notes" in payload
    assert payload["notes"] is None
    assert payload["priority"] == "high"


async def test_unset_fields_are_not_sent_at_all(patch_supabase):
    # Only priority was set — every other field must be entirely absent from the
    # update payload (not present-as-None), so it's left untouched in the DB.
    update = RequisitionUpdate(priority="low")
    await update_requisition(1, update, current_user={"user_id": "u1"})
    payload = patch_supabase["update_payload"]
    assert payload["priority"] == "low"
    for field in ("date", "requester", "section", "required_for", "status", "requisition_number", "notes"):
        assert field not in payload


async def test_items_key_never_reaches_the_requisitions_table_update(patch_supabase):
    update = RequisitionUpdate(priority="medium", items=[])
    await update_requisition(1, update, current_user={"user_id": "u1"})
    payload = patch_supabase["update_payload"]
    assert "items" not in payload
