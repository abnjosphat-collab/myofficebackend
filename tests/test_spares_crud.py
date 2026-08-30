# tests/test_spares_crud.py — create_spare, update_spare (the non-price-sync branches;
# the unit_price-triggered stock_issues sync is already covered by
# test_spares_price_sync.py), delete_spare, and get_spare had zero prior tests despite
# being the core CRUD surface of the lowest-coverage inventory/money router. Uses a
# response-queue fake: each handler makes a small, fixed sequence of
# supabase.table(...).execute() calls in a known order, so tests configure canned
# responses positionally and assert on what was actually sent (payload/filters), not
# just the final status.

import pytest
from fastapi import HTTPException

import app.routers.spares as spares_mod
from app.routers.spares import SpareCreate, SpareUpdate, create_spare, update_spare, delete_spare, get_spare


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

    def eq(self, col, val):
        self.state["filters"].append(("eq", col, val))
        return self

    def neq(self, col, val):
        self.state["filters"].append(("neq", col, val))
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        self.state["insert_payload"] = data
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        self.state["update_payload"] = data
        return self

    def delete(self):
        self._op = "delete"
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
    def _patch(responses):
        state = {"responses": list(responses), "filters": [], "calls": [], "insert_payload": None, "update_payload": None}
        monkeypatch.setattr(spares_mod, "supabase", _FakeSupabase(state))
        return state
    return _patch


def _spare(**overrides):
    base = dict(stock_code="SC-1", description="Widget", current_quantity=5, min_quantity=1, max_quantity=10, unit_price=9.99)
    base.update(overrides)
    return SpareCreate(**base)


# ─── create_spare ───────────────────────────────────────────────────────────────────

async def test_create_spare_happy_path_inserts_and_returns_row(patch_supabase):
    state = patch_supabase([[], [{"id": 1, "stock_code": "SC-1", "description": "Widget"}]])
    result = await create_spare(_spare(), current_user={"user_id": "u1"})
    assert result == {"id": 1, "stock_code": "SC-1", "description": "Widget"}
    assert state["calls"] == ["select", "insert"]
    assert state["insert_payload"]["stock_code"] == "SC-1"


async def test_create_spare_duplicate_stock_code_is_400(patch_supabase):
    state = patch_supabase([[{"id": 99}]])
    with pytest.raises(HTTPException) as exc_info:
        await create_spare(_spare(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 400
    assert "already exists" in exc_info.value.detail
    # Must not have attempted the insert after finding a duplicate.
    assert state["calls"] == ["select"]


async def test_create_spare_insert_returning_no_row_is_500(patch_supabase):
    patch_supabase([[], []])
    with pytest.raises(HTTPException) as exc_info:
        await create_spare(_spare(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


async def test_create_spare_filters_unknown_fields_out_of_the_insert_payload(patch_supabase):
    # SpareCreate has no fields outside _DB_COLUMNS today, but filter_for_db is the
    # guard against a future field addition leaking a column Supabase doesn't have —
    # confirm the insert payload only ever contains known DB columns.
    state = patch_supabase([[], [{"id": 1, "stock_code": "SC-1"}]])
    await create_spare(_spare(), current_user={"user_id": "u1"})
    assert set(state["insert_payload"].keys()) <= spares_mod._DB_COLUMNS


# ─── update_spare (non price-sync branches) ─────────────────────────────────────────

async def test_update_spare_not_found_is_404(patch_supabase):
    patch_supabase([[]])
    with pytest.raises(HTTPException) as exc_info:
        await update_spare(1, SpareUpdate(description="New"), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 404


async def test_update_spare_stock_code_conflict_is_400(patch_supabase):
    state = patch_supabase([
        [{"id": 1, "stock_code": "SC-OLD"}],   # get_or_404
        [{"id": 2}],                            # conflict check finds another row
    ])
    with pytest.raises(HTTPException) as exc_info:
        await update_spare(1, SpareUpdate(stock_code="SC-TAKEN"), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 400
    assert "already exists" in exc_info.value.detail
    assert state["calls"] == ["select", "select"]


async def test_update_spare_no_data_provided_is_400(patch_supabase):
    state = patch_supabase([[{"id": 1, "stock_code": "SC-1"}]])
    with pytest.raises(HTTPException) as exc_info:
        await update_spare(1, SpareUpdate(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 400
    # Only the existence check ran — never reached an update() call.
    assert state["calls"] == ["select"]


async def test_update_spare_happy_path_updates_and_returns_row(patch_supabase):
    state = patch_supabase([
        [{"id": 1, "stock_code": "SC-1"}],                          # get_or_404
        [{"id": 1, "stock_code": "SC-1", "description": "New"}],    # update
    ])
    result = await update_spare(1, SpareUpdate(description="New"), current_user={"user_id": "u1"})
    assert result["description"] == "New"
    assert state["update_payload"] == {"description": "New"}
    # No unit_price in this update -> price-sync RPC must never fire.
    assert "rpc_name" not in state


async def test_update_spare_empty_string_notes_is_dropped_not_saved(patch_supabase):
    # clean_data still strips a blank string — an update that only sends notes=""
    # ends up with an empty update_data dict and hits the "No data provided" 400, not
    # a silent no-op. (Deliberately different from the None case below.)
    state = patch_supabase([[{"id": 1, "stock_code": "SC-1"}]])
    with pytest.raises(HTTPException) as exc_info:
        await update_spare(1, SpareUpdate(notes=""), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 400


async def test_update_spare_explicit_none_clears_the_field(patch_supabase):
    # Regression test for the null-vs-unset bug (backend edec24a, reintroduced here,
    # fixed 2026-08-30): clean_data used to drop an explicit None the same way it
    # drops "", making "clear this field" indistinguishable from "field not sent" —
    # exclude_unset=True already handles "not sent"; an explicit None reaching
    # clean_data must reach the database as a real NULL.
    state = patch_supabase([
        [{"id": 1, "stock_code": "SC-1", "notes": "old note"}],
        [{"id": 1, "stock_code": "SC-1", "notes": None}],
    ])
    result = await update_spare(1, SpareUpdate(notes=None), current_user={"user_id": "u1"})
    assert state["update_payload"] == {"notes": None}
    assert result["notes"] is None


async def test_update_spare_returning_no_row_is_500(patch_supabase):
    patch_supabase([
        [{"id": 1, "stock_code": "SC-1"}],
        [],
    ])
    with pytest.raises(HTTPException) as exc_info:
        await update_spare(1, SpareUpdate(description="New"), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


# ─── delete_spare ───────────────────────────────────────────────────────────────────

async def test_delete_spare_happy_path(patch_supabase):
    state = patch_supabase([
        [{"id": 1, "stock_code": "SC-1"}],  # get_or_404
        [],                                   # delete
    ])
    result = await delete_spare(1, current_user={"user_id": "u1", "role": "manager"})
    assert result == {"message": "Spare part deleted successfully", "id": 1, "stock_code": "SC-1"}
    assert state["calls"] == ["select", "delete"]


async def test_delete_spare_not_found_is_404(patch_supabase):
    patch_supabase([[]])
    with pytest.raises(HTTPException) as exc_info:
        await delete_spare(1, current_user={"user_id": "u1", "role": "manager"})
    assert exc_info.value.status_code == 404


# ─── get_spare ──────────────────────────────────────────────────────────────────────

async def test_get_spare_happy_path_defaults_missing_categories(patch_supabase):
    patch_supabase([[{"id": 1, "stock_code": "SC-1"}]])
    result = await get_spare(1)
    assert result["stock_code"] == "SC-1"
    assert result["categories"] == []


async def test_get_spare_preserves_existing_categories(patch_supabase):
    patch_supabase([[{"id": 1, "stock_code": "SC-1", "categories": ["Bearings"]}]])
    result = await get_spare(1)
    assert result["categories"] == ["Bearings"]


async def test_get_spare_not_found_is_404(patch_supabase):
    patch_supabase([[]])
    with pytest.raises(HTTPException) as exc_info:
        await get_spare(999)
    assert exc_info.value.status_code == 404
