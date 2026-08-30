# tests/test_spares_price_sync.py — update_spare's price-sync-into-stock_issues branch
# (triggered whenever unit_price is part of a PUT payload) referenced an undefined
# `existing` variable: `existing.data[0].get('stock_code')`, left over from a refactor —
# `get_or_404`'s result was never assigned to anything. This raised a live NameError on
# every PUT /spares/{id} that included unit_price, surfacing as a generic 500 ("Error
# updating spare part") and silently breaking the price-sync feature entirely. Found via
# the 2026-08-30 rows()/one_row() pyright pass on this file (pyright's
# reportUndefinedVariable was already flagging it). Fixed by capturing get_or_404's
# result; this locks in that the OLD stock_code (fetched before the update) is what
# reaches the sync RPC, which matters when stock_code is changed in the same request.

import pytest

import app.routers.spares as spares_mod
from app.routers.spares import SpareUpdate, update_spare


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
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

    def execute(self):
        if self._mode == "update":
            return _Resp([{"id": 1, "stock_code": "SC-OLD", **self.state["update_payload"]}])
        # get_or_404's existence check — the spare's row BEFORE this update.
        return _Resp([{"id": 1, "stock_code": "SC-OLD", "unit_price": 10.0}])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, _name):
        return _FakeTable(self.state)

    def rpc(self, name, params):
        self.state["rpc_name"] = name
        self.state["rpc_params"] = params
        return self

    def execute(self):
        return _Resp(1)  # sync_issue_item_prices' updated_count


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {"update_payload": None, "rpc_name": None, "rpc_params": None}
    monkeypatch.setattr(spares_mod, "supabase", _FakeSupabase(state))
    return state


async def test_unit_price_update_syncs_using_the_pre_update_stock_code(patch_supabase):
    # Would previously raise NameError ("existing" was never assigned) before even
    # reaching the RPC call — this test fails on the old code, passes on the fix.
    update = SpareUpdate(unit_price=15.0)
    await update_spare(1, update, current_user={"user_id": "u1"})
    assert patch_supabase["rpc_name"] == "sync_issue_item_prices"
    assert patch_supabase["rpc_params"]["p_stock_code"] == "SC-OLD"
    assert patch_supabase["rpc_params"]["p_new_price"] == 15.0


async def test_update_without_unit_price_does_not_call_the_sync_rpc(patch_supabase):
    update = SpareUpdate(description="New description")
    await update_spare(1, update, current_user={"user_id": "u1"})
    assert patch_supabase["rpc_name"] is None
