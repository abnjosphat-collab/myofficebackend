# tests/test_sops_crud.py — list_sops, list_archived_sops, get_sop, create_sop,
# update_sop (incl. the manager-can-edit / admin-must-approve conditional role
# split), archive_sop, restore_sop.
#
# Uses the sanctioned "call the route coroutine directly against a fake supabase
# client" recipe (see test_leaves_crud.py) since Query()/Depends() resolution is
# bypassed when calling directly — route-level role gates (require_role(...) in
# the Depends() default) are declarations FastAPI enforces at request time, not
# something re-verified by calling the coroutine directly; the one role check
# genuinely IN the handler body (the approve-needs-admin conditional gate) is
# what's tested here, the same way test_leaves_crud.py tests its equivalent.

import pytest
from fastapi import HTTPException

import app.routers.sops as sops_mod
from app.routers.sops import (
    SopCreate, SopUpdate, SopSections,
    list_sops, list_archived_sops, get_sop, create_sop, update_sop,
    archive_sop, restore_sop,
)

pytestmark = pytest.mark.asyncio


# ─── Fake supabase — records every call for assertion ──────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _NotProxy:
    """`.not_.is_(...)` — records onto the same query, just prefixes the op name."""
    def __init__(self, query):
        self._query = query

    def is_(self, col, val):
        self._query._filters.append(("not.is_", col, val))
        return self._query


class _FakeQuery:
    def __init__(self, table_name, state, response_map):
        self.table_name = table_name
        self.state = state
        self._response_map = response_map
        self._filters = []
        self._order = None
        self._limit = None
        self._payload = None
        self._op = "select"

    def select(self, *a, **k): return self
    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self
    def is_(self, col, val):
        self._filters.append(("is_", col, val))
        return self
    @property
    def not_(self):
        return _NotProxy(self)
    def or_(self, expr):
        self._filters.append(("or_", expr))
        return self
    def order(self, col, desc=False):
        self._order = (col, desc)
        return self
    def limit(self, n):
        self._limit = n
        return self
    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self
    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def execute(self):
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        table_cfg = self._response_map.get(self.table_name, {})
        if self._op == "insert":
            return _Resp(table_cfg.get("insert_return", [{"id": "new-id", **(self._payload or {})}]))
        if self._op == "update":
            return _Resp(table_cfg.get("update_return", [{"id": "1", **(self._payload or {})}]))
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
        monkeypatch.setattr(sops_mod, "supabase", _FakeSupabase(state, response_map))
        return state
    return _patch


def _create_payload(**overrides):
    base = dict(code="SOP-OPS-001", title="Lockout / Tagout", department="Operations", owner="J. Moyo")
    base.update(overrides)
    return SopCreate(**base)


USER = {"user_id": "u1", "email": "manager@ozech.test", "role": "manager"}


# ─── list_sops / list_archived_sops ─────────────────────────────────────────────

async def test_list_sops_filters_to_non_deleted(patch_supabase):
    state = patch_supabase({"sop_documents": {"select_return": [{"id": "1", "code": "SOP-1"}]}})
    result = await list_sops()
    assert result == [{"id": "1", "code": "SOP-1"}]
    calls = [c for c in state["calls"] if c["table"] == "sop_documents"]
    assert ("is_", "deleted_at", "null") in calls[0]["filters"]


async def test_list_sops_applies_search_and_department_filters(patch_supabase):
    state = patch_supabase({"sop_documents": {"select_return": []}})
    await list_sops(search="lockout", department="Operations", status="draft", owner="J. Moyo")
    filters = state["calls"][0]["filters"]
    assert any(f[0] == "or_" for f in filters)
    assert ("eq", "department", "Operations") in filters
    assert ("eq", "status", "draft") in filters
    assert ("eq", "owner", "J. Moyo") in filters


async def test_list_archived_sops_uses_not_is_deleted(patch_supabase):
    patch_supabase({"sop_documents": {"select_return": [{"id": "2", "deleted_at": "2026-01-01T00:00:00"}]}})
    result = await list_archived_sops()
    assert result[0]["id"] == "2"


# ─── get_sop ─────────────────────────────────────────────────────────────────

async def test_get_sop_returns_document_and_revisions(patch_supabase):
    patch_supabase({
        "sop_documents": {"select_return": [{"id": "1", "code": "SOP-1"}]},
        "sop_revisions": {"select_return": [{"id": "r1", "revision_number": 2}, {"id": "r2", "revision_number": 1}]},
    })
    result = await get_sop("1")
    assert result["sop"]["id"] == "1"
    assert len(result["revisions"]) == 2


async def test_get_sop_404_when_missing(patch_supabase):
    patch_supabase({"sop_documents": {"select_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await get_sop("missing")
    assert exc_info.value.status_code == 404


# ─── create_sop ───────────────────────────────────────────────────────────────

async def test_create_sop_writes_document_and_first_revision(patch_supabase):
    state = patch_supabase({
        "sop_documents": {
            "select_return": [],  # duplicate-code check: none found
            "insert_return": [{"id": "sop-1", "code": "SOP-OPS-001", "title": "Lockout / Tagout"}],
        },
        "sop_revisions": {"select_return": [], "insert_return": [{"id": "rev-1"}]},
    })
    result = await create_sop(_create_payload(), current_user=USER)
    assert result["id"] == "sop-1"

    rev_insert = next(c for c in state["calls"] if c["table"] == "sop_revisions" and c["op"] == "insert")
    assert rev_insert["payload"]["revision_number"] == 1
    assert rev_insert["payload"]["author_email"] == "manager@ozech.test"
    assert rev_insert["payload"]["change_note"] == "Initial draft"


async def test_create_sop_rejects_duplicate_code(patch_supabase):
    patch_supabase({"sop_documents": {"select_return": [{"id": "existing"}]}})
    with pytest.raises(HTTPException) as exc_info:
        await create_sop(_create_payload(), current_user=USER)
    assert exc_info.value.status_code == 409


# ─── update_sop ───────────────────────────────────────────────────────────────

async def test_update_sop_saves_and_snapshots_a_revision(patch_supabase):
    state = patch_supabase({
        "sop_documents": {
            "select_return": [{"id": "sop-1", "code": "SOP-OPS-001"}],
            "update_return": [{"id": "sop-1", "code": "SOP-OPS-001", "title": "Updated Title"}],
        },
        "sop_revisions": {"select_return": [{"revision_number": 1}], "insert_return": [{"id": "rev-2"}]},
    })
    body = SopUpdate(title="Updated Title", change_note="Clarified step 3")
    result = await update_sop("sop-1", body, authorization=None, current_user=USER)
    assert result["title"] == "Updated Title"

    rev_insert = next(c for c in state["calls"] if c["table"] == "sop_revisions" and c["op"] == "insert")
    assert rev_insert["payload"]["revision_number"] == 2
    assert rev_insert["payload"]["change_note"] == "Clarified step 3"


async def test_update_sop_404_when_missing(patch_supabase):
    patch_supabase({"sop_documents": {"select_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await update_sop("missing", SopUpdate(title="x"), authorization=None, current_user=USER)
    assert exc_info.value.status_code == 404


async def test_update_sop_approving_without_admin_role_is_rejected(patch_supabase, monkeypatch):
    state = patch_supabase({"sop_documents": {"select_return": [{"id": "sop-1"}]}})

    async def _fake_gate(status, trigger_statuses, min_role, authorization, context="Status change"):
        if status in trigger_statuses:
            raise HTTPException(status_code=403, detail="Permission denied.")
        return None
    monkeypatch.setattr(sops_mod, "require_role_if_status_in", _fake_gate)

    with pytest.raises(HTTPException) as exc_info:
        await update_sop("sop-1", SopUpdate(status="effective"), authorization=None, current_user=USER)
    assert exc_info.value.status_code == 403
    assert [c for c in state["calls"] if c["op"] == "update"] == []


# ─── archive_sop / restore_sop ─────────────────────────────────────────────────

async def test_archive_sop_soft_deletes(patch_supabase):
    state = patch_supabase({
        "sop_documents": {
            "select_return": [{"id": "sop-1", "deleted_at": None}],
            "update_return": [{"id": "sop-1", "deleted_at": "2026-09-04T00:00:00"}],
        },
    })
    result = await archive_sop("sop-1", current_user=USER)
    assert result["deleted_at"] is not None
    update_call = next(c for c in state["calls"] if c["op"] == "update")
    assert "deleted_at" in update_call["payload"]


async def test_archive_sop_already_archived_is_rejected(patch_supabase):
    patch_supabase({"sop_documents": {"select_return": [{"id": "sop-1", "deleted_at": "2026-01-01T00:00:00"}]}})
    with pytest.raises(HTTPException) as exc_info:
        await archive_sop("sop-1", current_user=USER)
    assert exc_info.value.status_code == 400


async def test_restore_sop_clears_deleted_at(patch_supabase):
    state = patch_supabase({
        "sop_documents": {
            "select_return": [{"id": "sop-1", "deleted_at": "2026-01-01T00:00:00"}],
            "update_return": [{"id": "sop-1", "deleted_at": None}],
        },
    })
    result = await restore_sop("sop-1", current_user=USER)
    assert result["deleted_at"] is None
    update_call = next(c for c in state["calls"] if c["op"] == "update")
    assert update_call["payload"]["deleted_at"] is None


async def test_restore_sop_not_archived_is_rejected(patch_supabase):
    patch_supabase({"sop_documents": {"select_return": [{"id": "sop-1", "deleted_at": None}]}})
    with pytest.raises(HTTPException) as exc_info:
        await restore_sop("sop-1", current_user=USER)
    assert exc_info.value.status_code == 400
