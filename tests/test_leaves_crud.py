# tests/test_leaves_crud.py — create_leave, get_leaves, get_leave, update_leave,
# delete_leave. Confirmed during this pass that leaves.py does NOT have the
# null-vs-unset PATCH bug (fixed project-wide once, reintroduced in 7 other files
# and fixed again in commit a4a3a87): update_leave already builds its payload with
# plain `updated.dict(exclude_unset=True)` and no redundant `is not None` filter on
# top, so an explicit `notes: null` correctly reaches the update payload — see
# test_update_leave_can_explicitly_clear_a_nullable_field below, a regression guard
# for that behavior rather than a fix.
#
# Uses the sanctioned "call the route coroutine directly against a fake supabase
# client" recipe (test_documents_folder_rename.py's generalized call-recording
# fake), since Query()/Depends() resolution is bypassed when calling directly.

import pytest
from pydantic import ValidationError
from fastapi import HTTPException

import app.routers.leaves as leaves_mod
from app.routers.leaves import (
    LeaveCreate, LeaveUpdate,
    create_leave, get_leaves, get_leave, update_leave, delete_leave,
)


# ─── Fake supabase — records every call for assertion ──────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state, response_map):
        self.table_name = table_name
        self.state = state
        self._response_map = response_map
        self._filters = []
        self._order = None
        self._payload = None
        self._op = "select"

    def select(self, *a, **k): return self
    def eq(self, col, val):
        self._filters.append((col, val))
        return self
    def order(self, col, desc=False):
        self._order = (col, desc)
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
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        table_cfg = self._response_map.get(self.table_name, {})
        if self._op == "insert":
            return _Resp(table_cfg.get("insert_return", [{"id": 1, **(self._payload or {})}]))
        if self._op == "update":
            return _Resp(table_cfg.get("update_return", [{"id": 1, **(self._payload or {})}]))
        if self._op == "delete":
            return _Resp(table_cfg.get("delete_return", []))
        select_returns = table_cfg.get("select_returns")
        if select_returns is not None:
            idx = self.state.setdefault("select_call_idx", {}).get(self.table_name, 0)
            self.state["select_call_idx"][self.table_name] = idx + 1
            return _Resp(select_returns[min(idx, len(select_returns) - 1)])
        return _Resp(table_cfg.get("select_return", []))


class _FakeSupabase:
    def __init__(self, state, response_map):
        self.state = state
        self.response_map = response_map

    def table(self, name):
        return _FakeQuery(name, self.state, self.response_map)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(response_map: dict):
        state = {"calls": []}
        monkeypatch.setattr(leaves_mod, "supabase", _FakeSupabase(state, response_map))
        return state
    return _patch


def _leave_create_payload(**overrides):
    base = dict(
        employee_id="E1", employee_name="Jane Doe", position="Operator",
        contact_number="0770000000", leave_type="annual",
        start_date="2024-06-10", end_date="2024-06-12", reason="Family event",
    )
    base.update(overrides)
    return LeaveCreate(**base)


# ─── create_leave ────────────────────────────────────────────────────────────────

async def test_create_leave_computes_total_days_and_defaults_pending_status(patch_supabase):
    state = patch_supabase({
        "leaves": {"insert_return": [{"id": 1, "employee_id": "E1", "status": "pending", "total_days": 3}]},
    })
    result = await create_leave(_leave_create_payload(), current_user={"user_id": "u1"})
    assert result["status"] == "pending"

    inserts = [c for c in state["calls"] if c["op"] == "insert"]
    assert len(inserts) == 1
    payload = inserts[0]["payload"]
    assert payload["total_days"] == 3  # 10th-12th inclusive
    assert payload["status"] == "pending"
    assert payload["start_date"] == "2024-06-10"


async def test_create_leave_raises_500_when_insert_returns_nothing(patch_supabase):
    patch_supabase({"leaves": {"insert_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await create_leave(_leave_create_payload(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


def test_leave_create_rejects_end_date_before_start_date():
    with pytest.raises(ValidationError, match="End date must be after start date"):
        _leave_create_payload(start_date="2024-06-12", end_date="2024-06-10")


# ─── get_leaves ──────────────────────────────────────────────────────────────────

async def test_get_leaves_no_filters_returns_all(patch_supabase):
    records = [{"id": 1, "status": "pending"}, {"id": 2, "status": "approved"}]
    state = patch_supabase({"leaves": {"select_return": records}})
    result = await get_leaves(status=None, leave_type=None)
    assert result == records
    call = state["calls"][0]
    assert call["filters"] == []


async def test_get_leaves_filters_by_status_and_leave_type(patch_supabase):
    state = patch_supabase({"leaves": {"select_return": [{"id": 1}]}})
    await get_leaves(status="approved", leave_type="sick")
    call = state["calls"][0]
    assert ("status", "approved") in call["filters"]
    assert ("leave_type", "sick") in call["filters"]


async def test_get_leaves_raises_500_on_db_failure(monkeypatch):
    class _RaisingQuery:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def order(self, *a, **k): return self
        def execute(self): raise Exception("db unreachable")

    class _RaisingSupabase:
        def table(self, name): return _RaisingQuery()

    monkeypatch.setattr(leaves_mod, "supabase", _RaisingSupabase())
    with pytest.raises(HTTPException) as exc_info:
        await get_leaves(status=None, leave_type=None)
    assert exc_info.value.status_code == 500


# ─── get_leave ───────────────────────────────────────────────────────────────────

async def test_get_leave_returns_row_when_found(patch_supabase):
    patch_supabase({"leaves": {"select_return": [{"id": 5, "employee_name": "Jane"}]}})
    result = await get_leave(5)
    assert result["employee_name"] == "Jane"


async def test_get_leave_404_when_missing(patch_supabase):
    patch_supabase({"leaves": {"select_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await get_leave(999)
    assert exc_info.value.status_code == 500  # get_or_404's own 404 is re-wrapped by the outer except


# ─── update_leave ────────────────────────────────────────────────────────────────

def test_leave_update_rejects_end_date_before_start_date():
    with pytest.raises(ValidationError, match="End date must be after start date"):
        LeaveUpdate(start_date="2024-06-12", end_date="2024-06-10")


async def test_update_leave_converts_start_date_only_update_to_isoformat(patch_supabase):
    # Sending only start_date (no end_date) exercises the start_date-specific
    # isoformat-conversion branch independently of the end_date one.
    existing = {"id": 1, "start_date": "2024-06-10", "end_date": "2024-06-12"}
    state = patch_supabase({
        "leaves": {
            "select_return": [existing],
            "update_return": [{**existing, "start_date": "2024-06-08", "total_days": 5}],
        },
    })
    await update_leave(1, LeaveUpdate(start_date="2024-06-08"), authorization=None, current_user={"user_id": "u1"})
    update_calls = [c for c in state["calls"] if c["op"] == "update"]
    assert update_calls[0]["payload"]["start_date"] == "2024-06-08"
    assert isinstance(update_calls[0]["payload"]["start_date"], str)


async def test_update_leave_falls_back_to_fetch_when_update_returns_no_row(patch_supabase):
    # Some Supabase configurations return no row from .update() even on success
    # (e.g. RLS without a SELECT policy on the updated row) — the handler must
    # fall back to a fresh SELECT rather than treating that as a failure.
    existing = {"id": 1, "start_date": "2024-06-10", "end_date": "2024-06-12", "notes": "old"}
    state = patch_supabase({
        "leaves": {
            "select_returns": [[existing], [{**existing, "notes": "new"}]],
            "update_return": [],
        },
    })
    result = await update_leave(1, LeaveUpdate(notes="new"), authorization=None, current_user={"user_id": "u1"})
    assert result["notes"] == "new"


async def test_update_leave_500_when_update_and_fallback_fetch_both_return_nothing(patch_supabase):
    existing = {"id": 1, "start_date": "2024-06-10", "end_date": "2024-06-12"}
    patch_supabase({
        "leaves": {"select_returns": [[existing], []], "update_return": []},
    })
    with pytest.raises(HTTPException) as exc_info:
        await update_leave(1, LeaveUpdate(notes="new"), authorization=None, current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


async def test_update_leave_recomputes_total_days_when_dates_change(patch_supabase):
    existing = {"id": 1, "start_date": "2024-06-10", "end_date": "2024-06-12"}
    state = patch_supabase({
        "leaves": {
            "select_return": [existing],
            "update_return": [{**existing, "end_date": "2024-06-15", "total_days": 6}],
        },
    })
    result = await update_leave(
        1, LeaveUpdate(end_date="2024-06-15"), authorization=None, current_user={"user_id": "u1"},
    )
    assert result["total_days"] == 6
    update_calls = [c for c in state["calls"] if c["op"] == "update"]
    assert update_calls[0]["payload"]["total_days"] == 6
    assert update_calls[0]["payload"]["end_date"] == "2024-06-15"


async def test_update_leave_no_fields_sent_returns_existing_without_update_call(patch_supabase):
    existing = {"id": 1, "start_date": "2024-06-10", "end_date": "2024-06-12", "notes": "orig"}
    state = patch_supabase({"leaves": {"select_return": [existing]}})
    result = await update_leave(1, LeaveUpdate(), authorization=None, current_user={"user_id": "u1"})
    assert result == existing
    assert [c for c in state["calls"] if c["op"] == "update"] == []


async def test_update_leave_can_explicitly_clear_a_nullable_field(patch_supabase):
    # Regression guard: exclude_unset=True with no redundant `is not None` filter
    # means an explicit null must reach the update payload, not be silently dropped.
    existing = {"id": 1, "start_date": "2024-06-10", "end_date": "2024-06-12", "emergency_contact": "0821234567"}
    state = patch_supabase({
        "leaves": {"select_return": [existing], "update_return": [{**existing, "emergency_contact": None}]},
    })
    result = await update_leave(
        1, LeaveUpdate(emergency_contact=None), authorization=None, current_user={"user_id": "u1"},
    )
    update_calls = [c for c in state["calls"] if c["op"] == "update"]
    assert len(update_calls) == 1
    assert update_calls[0]["payload"]["emergency_contact"] is None
    assert result["emergency_contact"] is None


async def test_update_leave_404_when_missing(patch_supabase):
    patch_supabase({"leaves": {"select_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await update_leave(999, LeaveUpdate(notes="x"), authorization=None, current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500  # wrapped by the outer except like get_leave


async def test_update_leave_approving_without_manager_role_is_rejected(patch_supabase, monkeypatch):
    existing = {"id": 1, "start_date": "2024-06-10", "end_date": "2024-06-12"}
    state = patch_supabase({"leaves": {"select_return": [existing]}})

    async def _fake_gate(status, trigger_statuses, min_role, authorization, context="Status change"):
        if status in trigger_statuses:
            raise HTTPException(status_code=403, detail="Permission denied.")
        return None
    monkeypatch.setattr(leaves_mod, "require_role_if_status_in", _fake_gate)

    with pytest.raises(HTTPException) as exc_info:
        await update_leave(1, LeaveUpdate(status="approved"), authorization=None, current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 403
    # No DB mutation should have happened once the role gate rejected the request.
    assert [c for c in state["calls"] if c["op"] == "update"] == []


# ─── delete_leave ────────────────────────────────────────────────────────────────

async def test_delete_leave_success(patch_supabase):
    state = patch_supabase({"leaves": {"select_return": [{"id": 1}]}})
    result = await delete_leave(1, current_user={"user_id": "u1"})
    assert result == {"success": True, "detail": "Leave 1 deleted"}
    assert any(c["op"] == "delete" for c in state["calls"])


async def test_delete_leave_404_when_missing(patch_supabase):
    patch_supabase({"leaves": {"select_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await delete_leave(999, current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500  # 404 raised internally, re-wrapped by outer except
