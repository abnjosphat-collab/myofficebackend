# tests/test_spares_bulk_create.py — bulk_create_spares (POST /spares/bulk) is the
# heavy-import path (4000+ row spreadsheets) with zero prior tests: the skip-existing
# split, the upsert new-vs-update split, and the batch-insert-falls-back-to-row-by-row
# error path were all unverified. The route is decorated with @limiter.limit and
# declares `request: Request` — the slowapi wrapper requires a real starlette Request
# UNLESS the limiter is disabled, so `disable_limiter` flips `limiter.enabled = False`
# for the duration of each test (restored after) rather than constructing a fake
# Request, matching what the decorator actually branches on.

import pytest
from fastapi import HTTPException

import app.routers.spares as spares_mod
from app.rate_limit import limiter
from app.routers.spares import BulkSpareCreate, SpareCreate, bulk_create_spares


@pytest.fixture(autouse=True)
def disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Tracks select/insert/update calls against the spares table for assertions."""

    def __init__(self, state):
        self.state = state
        self._op = "select"
        self._payload = None
        self._filters = []

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in_", col, list(vals)))
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data if isinstance(data, list) else [data]
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def execute(self):
        self.state["calls"].append({"op": self._op, "payload": self._payload, "filters": list(self._filters)})
        if self._op == "select":
            existing = self.state["existing_codes"]
            queried = next((v for (kind, col, v) in self._filters if kind == "in_" and col == "stock_code"), [])
            matched = [{"stock_code": c} for c in queried if c in existing]
            return _Resp(matched)
        if self._op == "insert":
            fail_codes = self.state.get("fail_insert_codes", set())
            if any(item.get("stock_code") in fail_codes for item in self._payload):
                raise RuntimeError("simulated insert failure")
            return _Resp(self._payload)
        if self._op == "update":
            fail_codes = self.state.get("fail_update_codes", set())
            target = next((v for (kind, col, v) in self._filters if kind == "eq" and col == "stock_code"), None)
            if target in fail_codes:
                raise RuntimeError("simulated update failure")
            return _Resp([self._payload])
        return _Resp([])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, _name):
        return _FakeQuery(self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(existing_codes=(), fail_insert_codes=(), fail_update_codes=()):
        state = {
            "calls": [],
            "existing_codes": set(existing_codes),
            "fail_insert_codes": set(fail_insert_codes),
            "fail_update_codes": set(fail_update_codes),
        }
        monkeypatch.setattr(spares_mod, "supabase", _FakeSupabase(state))
        return state
    return _patch


def _item(code, **overrides):
    base = dict(stock_code=code, description=f"Part {code}", current_quantity=1, min_quantity=1, max_quantity=5, unit_price=10.0)
    base.update(overrides)
    return SpareCreate(**base)


async def _call(payload, current_user=None):
    return await bulk_create_spares(request=None, payload=payload, current_user=current_user or {"user_id": "u1"})


# ─── empty payload ──────────────────────────────────────────────────────────────────

async def test_empty_items_returns_zeroed_summary_without_any_query(patch_supabase):
    state = patch_supabase()
    result = await _call(BulkSpareCreate(items=[]))
    assert result == {"created": 0, "skipped": 0, "errors": 0, "total": 0}
    assert state["calls"] == []


# ─── skip_existing (default, upsert=False) ─────────────────────────────────────────

async def test_skip_existing_splits_new_from_already_present_codes(patch_supabase):
    state = patch_supabase(existing_codes={"SC-2"})
    payload = BulkSpareCreate(items=[_item("SC-1"), _item("SC-2"), _item("SC-3")], skip_existing=True)
    result = await _call(payload)
    assert result == {"created": 2, "updated": 0, "skipped": 1, "errors": 0, "total": 3}
    insert_calls = [c for c in state["calls"] if c["op"] == "insert"]
    assert len(insert_calls) == 1
    inserted_codes = {row["stock_code"] for row in insert_calls[0]["payload"]}
    assert inserted_codes == {"SC-1", "SC-3"}


async def test_skip_existing_false_inserts_everything_without_a_lookup(patch_supabase):
    state = patch_supabase(existing_codes={"SC-1"})
    payload = BulkSpareCreate(items=[_item("SC-1"), _item("SC-2")], skip_existing=False)
    result = await _call(payload)
    assert result["created"] == 2
    assert result["skipped"] == 0
    # No existence-check select should have run at all.
    assert all(c["op"] != "select" for c in state["calls"])


async def test_batch_insert_failure_falls_back_to_row_by_row_and_counts_errors(patch_supabase):
    # One item's stock_code triggers a simulated insert failure for the whole batch;
    # the row-by-row fallback must still create the good rows and count only the bad one.
    state = patch_supabase(fail_insert_codes={"SC-BAD"})
    payload = BulkSpareCreate(items=[_item("SC-1"), _item("SC-BAD"), _item("SC-3")], skip_existing=True)
    result = await _call(payload)
    assert result["created"] == 2
    assert result["errors"] == 1
    assert result["total"] == 3


# ─── upsert=True ────────────────────────────────────────────────────────────────────

async def test_upsert_creates_new_and_updates_existing(patch_supabase):
    state = patch_supabase(existing_codes={"SC-2"})
    payload = BulkSpareCreate(items=[_item("SC-1"), _item("SC-2", description="Updated desc")], upsert=True)
    result = await _call(payload)
    assert result["created"] == 1
    assert result["updated"] == 1
    assert result["errors"] == 0

    update_calls = [c for c in state["calls"] if c["op"] == "update"]
    assert len(update_calls) == 1
    # stock_code itself must never be part of the update payload (it's the match key).
    assert "stock_code" not in update_calls[0]["payload"]
    assert update_calls[0]["payload"]["description"] == "Updated desc"
    assert ("eq", "stock_code", "SC-2") in update_calls[0]["filters"]


async def test_upsert_update_failure_is_counted_as_an_error(patch_supabase):
    state = patch_supabase(existing_codes={"SC-2"}, fail_update_codes={"SC-2"})
    payload = BulkSpareCreate(items=[_item("SC-2")], upsert=True)
    result = await _call(payload)
    assert result["updated"] == 0
    assert result["errors"] == 1


async def test_upsert_with_no_existing_codes_only_creates(patch_supabase):
    state = patch_supabase(existing_codes=set())
    payload = BulkSpareCreate(items=[_item("SC-1"), _item("SC-2")], upsert=True)
    result = await _call(payload)
    assert result["created"] == 2
    assert result["updated"] == 0
