# tests/test_spares_exception_paths.py — the remaining uncovered `except Exception`
# branches across spares.py that the happy-path/validation-error test files (crud,
# bulk_create, price_sync, saved_requisitions, suggestions_stats) don't reach: a raw DB
# failure (not a 404/400/409) inside get_spares, get_spare, bulk_create_spares'
# outer/upsert-insert paths, create_spare, update_spare (both its own update call and
# the price-sync RPC's own non-fatal swallow), delete_spare, and the saved-requisition
# update path — plus /spares/test/connection, which had zero coverage at all.

import pytest
from fastapi import HTTPException

import app.routers.spares as spares_mod
from app.rate_limit import limiter
from app.routers.spares import (
    BulkSpareCreate, SpareCreate, SpareUpdate, SavedSpareReqCreate,
    get_spares, get_spare, bulk_create_spares, create_spare, update_spare,
    delete_spare, create_saved_spare_requisition, update_saved_spare_requisition,
    test_connection,
)


class _Resp:
    def __init__(self, data):
        self.data = data


class _RaisingQuery:
    """Every chained call returns self; execute() always raises."""
    def __getattr__(self, _name):
        return lambda *_a, **_k: self

    def execute(self):
        raise RuntimeError("simulated db failure")


class _RaisingSupabase:
    def table(self, _name):
        return _RaisingQuery()


@pytest.fixture
def raising_supabase(monkeypatch):
    monkeypatch.setattr(spares_mod, "supabase", _RaisingSupabase())


# ─── get_spares / get_spare ─────────────────────────────────────────────────────────

async def test_get_spares_db_failure_is_500(raising_supabase):
    with pytest.raises(HTTPException) as exc:
        await get_spares(search=None, category=None, priority=None, limit=100_000, offset=0)
    assert exc.value.status_code == 500


async def test_get_spare_db_failure_is_500_not_404(raising_supabase):
    # get_or_404 only raises 404 for an empty result — a raw execute() failure
    # propagates as-is and must still surface as a 500, not be misread as "not found".
    with pytest.raises(HTTPException) as exc:
        await get_spare(1)
    assert exc.value.status_code == 500


# ─── bulk_create_spares ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


def _item(code, **overrides):
    base = dict(stock_code=code, description=f"Part {code}", current_quantity=1,
                min_quantity=1, max_quantity=5, unit_price=10.0)
    base.update(overrides)
    return SpareCreate(**base)


async def test_bulk_create_outer_failure_is_500(raising_supabase):
    # The existence-check IN() query itself fails, outside either insert/update
    # try/except block — must propagate to the route's own outer 500, not be silently
    # eaten.
    payload = BulkSpareCreate(items=[_item("SC-1")], skip_existing=True)
    with pytest.raises(HTTPException) as exc:
        await bulk_create_spares(request=None, payload=payload, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


class _UpsertInsertFailQuery:
    def __init__(self, state):
        self.state = state
        self._op = None
        self._payload = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def in_(self, *_a, **_k):
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data if isinstance(data, list) else [data]
        return self

    def update(self, data):
        self._op = "update"
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        if self._op == "select":
            return _Resp([])  # nothing pre-exists -> every item is "new"
        if self._op == "insert":
            self.state["insert_attempts"] += 1
            fail_codes = self.state.get("fail_codes", set())
            if any(item.get("stock_code") in fail_codes for item in self._payload):
                raise RuntimeError("simulated insert failure")
            return _Resp(self._payload)
        return _Resp([self._payload])


async def test_bulk_upsert_new_item_insert_failure_falls_back_row_by_row(monkeypatch):
    # Distinct from the SKIP-branch's own row-by-row fallback (already covered in
    # test_spares_bulk_create.py) — this is the UPSERT branch's separate "Step 2:
    # batch-insert brand-new rows" fallback path.
    state = {"insert_attempts": 0, "fail_codes": {"SC-1"}}
    monkeypatch.setattr(spares_mod, "supabase",
                         type("S", (), {"table": lambda self, _n: _UpsertInsertFailQuery(state)})())
    payload = BulkSpareCreate(items=[_item("SC-1")], upsert=True)
    result = await bulk_create_spares(request=None, payload=payload, current_user={"user_id": "u1"})
    assert result["created"] == 0
    assert result["errors"] == 1
    # Batch attempt, then the row-by-row retry for the same single item.
    assert state["insert_attempts"] == 2


async def test_bulk_upsert_row_by_row_retry_succeeds_for_a_good_item(monkeypatch):
    # The whole batch fails once (triggering the row-by-row fallback), but the
    # fallback's own per-item retry must still succeed for items that aren't
    # individually broken — not every row-by-row retry is doomed to fail too.
    state = {"insert_attempts": 0, "fail_codes": {"SC-BATCH-TRIGGER"}}
    monkeypatch.setattr(spares_mod, "supabase",
                         type("S", (), {"table": lambda self, _n: _UpsertInsertFailQuery(state)})())
    payload = BulkSpareCreate(
        items=[_item("SC-BATCH-TRIGGER"), _item("SC-GOOD")], upsert=True,
    )
    result = await bulk_create_spares(request=None, payload=payload, current_user={"user_id": "u1"})
    assert result["created"] == 1
    assert result["errors"] == 1


# ─── create_spare ───────────────────────────────────────────────────────────────────

async def test_create_spare_db_failure_is_500(raising_supabase):
    payload = SpareCreate(stock_code="SC-1", description="Widget")
    with pytest.raises(HTTPException) as exc:
        await create_spare(payload, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── update_spare ───────────────────────────────────────────────────────────────────

class _UpdateSpareQuery:
    def __init__(self, mode: str):
        self.mode = mode  # "update_fails" or "rpc_fails"
        self._op = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def neq(self, *_a, **_k):
        return self

    def update(self, _data):
        self._op = "update"
        return self

    def execute(self):
        if self._op == "select":
            return _Resp([{"id": 1, "stock_code": "SC-OLD", "unit_price": 10.0}])
        if self.mode == "update_fails":
            raise RuntimeError("simulated update failure")
        return _Resp([{"id": 1, "stock_code": "SC-OLD", "description": "New"}])


class _UpdateSpareSupabase:
    def __init__(self, mode: str):
        self.mode = mode

    def table(self, _name):
        return _UpdateSpareQuery(self.mode)

    def rpc(self, *_a, **_k):
        raise RuntimeError("simulated rpc failure")


async def test_update_spare_db_failure_is_500(monkeypatch):
    monkeypatch.setattr(spares_mod, "supabase", _UpdateSpareSupabase("update_fails"))
    update = SpareUpdate(description="New")
    with pytest.raises(HTTPException) as exc:
        await update_spare(1, update, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_update_spare_price_sync_rpc_failure_is_non_fatal(monkeypatch):
    # The comment above this except block calls it non-fatal on purpose (the sync
    # function may not be migrated in yet) — the update itself must still succeed and
    # return the updated row even though the RPC call blew up.
    monkeypatch.setattr(spares_mod, "supabase", _UpdateSpareSupabase("rpc_fails"))
    update = SpareUpdate(unit_price=15.0)
    result = await update_spare(1, update, current_user={"user_id": "u1"})
    assert result["description"] == "New"


# ─── delete_spare ───────────────────────────────────────────────────────────────────

class _DeleteSpareQuery:
    def __init__(self):
        self._op = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        if self._op == "select":
            return _Resp([{"id": 1, "stock_code": "SC-1"}])
        raise RuntimeError("simulated delete failure")


async def test_delete_spare_db_failure_is_500(monkeypatch):
    monkeypatch.setattr(spares_mod, "supabase",
                         type("S", (), {"table": lambda self, _n: _DeleteSpareQuery()})())
    with pytest.raises(HTTPException) as exc:
        await delete_spare(1, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── saved spare requisitions ───────────────────────────────────────────────────────

async def test_create_saved_requisition_db_failure_is_500(raising_supabase):
    # Distinct from insert-returning-None-is-500 (already covered in
    # test_spares_saved_requisitions.py) — here the execute() call itself raises.
    payload = SavedSpareReqCreate(name="Req 1")
    with pytest.raises(HTTPException) as exc:
        await create_saved_spare_requisition(payload, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_update_saved_requisition_db_failure_is_500(raising_supabase):
    payload = SavedSpareReqCreate(name="Req 1")
    with pytest.raises(HTTPException) as exc:
        await update_saved_spare_requisition("req-1", payload, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── test_connection ────────────────────────────────────────────────────────────────

async def test_connection_healthy_path(monkeypatch):
    monkeypatch.setattr(
        spares_mod, "supabase",
        type("S", (), {"table": lambda self, _n: type(
            "Q", (), {
                "select": lambda self, *a, **k: self,
                "limit": lambda self, *a, **k: self,
                "execute": lambda self: _Resp([{"id": 1}]),
            })()})())
    result = await test_connection()
    assert result["status"] == "ok"
    assert result["database"] == "connected"
    assert result["record_count"] == 1


async def test_connection_db_error_path(raising_supabase):
    result = await test_connection()
    assert result["status"] == "error"
    assert result["database"] == "disconnected"
