# tests/test_spares_saved_requisitions.py — the saved-spare-requisition CRUD
# (GET/POST/PUT/DELETE /spares/saved-requisitions) had zero prior tests. Covers the
# create/update timestamp shape, the update-not-found -> 404 branch, and the
# get-list's documented graceful-degradation comment ("table may not exist yet" ->
# return [] on error) — that one IS an already-documented intentional fallback, unlike
# the two flagged-but-unfixed instances in test_spares_suggestions_stats.py, so it's
# exercised here without a flag.

import pytest
from fastapi import HTTPException

import app.routers.spares as spares_mod
from app.routers.spares import (
    SavedSpareReqCreate,
    get_saved_spare_requisitions,
    create_saved_spare_requisition,
    update_saved_spare_requisition,
    delete_saved_spare_requisition,
)


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, state):
        self.state = state
        self._op = "select"
        self._payload = None
        self._filters = []

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def order(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        self.state["calls"].append({"op": self._op, "payload": self._payload, "filters": list(self._filters)})
        if self.state.get("raise_error"):
            raise RuntimeError("simulated db failure")
        if self._op == "select":
            return _Resp(self.state["list_data"])
        if self._op == "insert":
            return _Resp(self.state["insert_return"])
        if self._op == "update":
            return _Resp(self.state["update_return"])
        return _Resp([])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, _name):
        return _FakeQuery(self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(list_data=None, insert_return=None, update_return=None, raise_error=False):
        state = {
            "calls": [],
            "list_data": list_data or [],
            "insert_return": insert_return if insert_return is not None else [],
            "update_return": update_return if update_return is not None else [],
            "raise_error": raise_error,
        }
        monkeypatch.setattr(spares_mod, "supabase", _FakeSupabase(state))
        return state
    return _patch


def _req(**overrides):
    base = dict(name="Q1 restock", requester="Bob", lines=[{"stock_code": "SC-1", "qty": 2}], grand_total=100.0)
    base.update(overrides)
    return SavedSpareReqCreate(**base)


# ─── get_saved_spare_requisitions ───────────────────────────────────────────────────

async def test_get_saved_requisitions_happy_path(patch_supabase):
    patch_supabase(list_data=[{"id": "r1", "name": "Q1 restock"}])
    result = await get_saved_spare_requisitions()
    assert result == [{"id": "r1", "name": "Q1 restock"}]


async def test_get_saved_requisitions_table_missing_degrades_to_empty_list(patch_supabase):
    patch_supabase(raise_error=True)
    result = await get_saved_spare_requisitions()
    assert result == []


# ─── create_saved_spare_requisition ─────────────────────────────────────────────────

async def test_create_saved_requisition_stamps_saved_at_and_updated_at(patch_supabase):
    state = patch_supabase(insert_return=[{"id": "r1", "name": "Q1 restock", "saved_at": "x", "updated_at": "x"}])
    result = await create_saved_spare_requisition(_req(), current_user={"user_id": "u1"})
    assert result["id"] == "r1"
    payload = state["calls"][0]["payload"]
    assert "saved_at" in payload
    assert "updated_at" in payload
    assert payload["name"] == "Q1 restock"


async def test_create_saved_requisition_insert_failure_is_500(patch_supabase):
    patch_supabase(insert_return=[])
    with pytest.raises(HTTPException) as exc_info:
        await create_saved_spare_requisition(_req(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


# ─── update_saved_spare_requisition ─────────────────────────────────────────────────

async def test_update_saved_requisition_happy_path(patch_supabase):
    state = patch_supabase(update_return=[{"id": "r1", "name": "Renamed"}])
    result = await update_saved_spare_requisition("r1", _req(name="Renamed"), current_user={"user_id": "u1"})
    assert result["name"] == "Renamed"
    payload = state["calls"][0]["payload"]
    assert "updated_at" in payload
    assert "saved_at" not in payload  # only stamped on create, not on update
    assert ("id", "r1") in state["calls"][0]["filters"]


async def test_update_saved_requisition_not_found_is_404(patch_supabase):
    patch_supabase(update_return=[])
    with pytest.raises(HTTPException) as exc_info:
        await update_saved_spare_requisition("missing", _req(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 404


# ─── delete_saved_spare_requisition ─────────────────────────────────────────────────

async def test_delete_saved_requisition_happy_path(patch_supabase):
    state = patch_supabase()
    result = await delete_saved_spare_requisition("r1", current_user={"user_id": "u1", "role": "manager"})
    assert result == {"message": "Deleted", "id": "r1"}
    assert state["calls"][0]["op"] == "delete"
    assert ("id", "r1") in state["calls"][0]["filters"]


async def test_delete_saved_requisition_db_error_is_500(patch_supabase):
    patch_supabase(raise_error=True)
    with pytest.raises(HTTPException) as exc_info:
        await delete_saved_spare_requisition("r1", current_user={"user_id": "u1", "role": "manager"})
    assert exc_info.value.status_code == 500
