# tests/test_compliance_crud.py — compliance.py's CRUD endpoints had zero tests beyond
# the pure _compute_status window logic (test_compliance_lubrication_status.py). Covers
# the auto-refreshed status on every list/get (recomputed from expiry_date, not trusted
# from storage), the status-filter applied *after* that refresh, create's computed
# status on insert, update's exclude_unset null-clearing + status recompute only when
# expiry_date is actually part of the payload, and 404s.

import pytest

import app.routers.compliance as compliance_mod
from app.routers.compliance import (
    ComplianceCreate, ComplianceUpdate,
    get_compliance, get_compliance_item, create_compliance, update_compliance, delete_compliance,
)
from fastapi import HTTPException
from datetime import date, timedelta


def _iso(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state):
        self.table_name = table_name
        self.state = state
        self._filters = []
        self._mode = "select"
        self._payload = None

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def insert(self, data):
        self._mode = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._mode = "update"
        self._payload = data
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def execute(self):
        table = self.state.setdefault(self.table_name, [])
        self.state.setdefault("calls", []).append(
            {"mode": self._mode, "filters": list(self._filters), "payload": self._payload}
        )
        if self._mode == "insert":
            row = dict(self._payload)
            next_id = self.state.setdefault("_next_id", [1])
            row["id"] = next_id[0]
            next_id[0] += 1
            table.append(row)
            return _Resp([row])

        matches = [r for r in table if all(r.get(c) == v for c, v in self._filters)]
        if self._mode == "select":
            return _Resp(matches)
        if self._mode == "update":
            for r in matches:
                r.update(self._payload)
            return _Resp(matches)
        if self._mode == "delete":
            for r in matches:
                table.remove(r)
            return _Resp(matches)
        return _Resp([])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(name, self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {}

    def _patch(items=None):
        state["compliance_register"] = list(items or [])
        monkeypatch.setattr(compliance_mod, "supabase", _FakeSupabase(state))
        return state

    return _patch


def _user():
    return {"user_id": "u-1", "email": "u@x.com", "role": "user"}


def _manager():
    return {"user_id": "m-1", "email": "m@x.com", "role": "manager"}


# ─── get_compliance ──────────────────────────────────────────────────────────────────

async def test_list_recomputes_status_from_expiry_not_stored_value(patch_supabase):
    # The stored "status" says current, but the expiry date says otherwise — the
    # handler must recompute, not trust storage.
    patch_supabase([{"id": 1, "equipment_name": "Crane", "expiry_date": _iso(-5), "status": "current"}])
    result = await get_compliance(status=None)
    assert result[0]["status"] == "overdue"


async def test_list_filters_by_recomputed_status(patch_supabase):
    patch_supabase([
        {"id": 1, "equipment_name": "Crane", "expiry_date": _iso(-5), "status": "current"},   # -> overdue
        {"id": 2, "equipment_name": "Hoist", "expiry_date": _iso(100), "status": "current"},   # -> current
    ])
    result = await get_compliance(status="overdue")
    assert [r["id"] for r in result] == [1]


async def test_list_no_expiry_is_current(patch_supabase):
    patch_supabase([{"id": 1, "equipment_name": "Crane", "expiry_date": None, "status": "overdue"}])
    result = await get_compliance(status=None)
    assert result[0]["status"] == "current"


# ─── get_compliance_item ─────────────────────────────────────────────────────────────

async def test_get_item_found_recomputes_status(patch_supabase):
    patch_supabase([{"id": 1, "equipment_name": "Crane", "expiry_date": _iso(10), "status": "overdue"}])
    result = await get_compliance_item(1)
    assert result["status"] == "due_soon"


async def test_get_item_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await get_compliance_item(999)
    assert exc.value.status_code == 404


# ─── create_compliance ───────────────────────────────────────────────────────────────

async def test_create_computes_status_on_insert(patch_supabase):
    patch_supabase([])
    data = ComplianceCreate(equipment_name="Crane", inspection_type="Annual", expiry_date=_iso(-1))
    result = await create_compliance(data, current_user=_user())
    assert result["status"] == "overdue"


async def test_create_excludes_none_fields_from_payload(patch_supabase):
    state = patch_supabase([])
    data = ComplianceCreate(equipment_name="Crane", inspection_type="Annual", expiry_date=_iso(60))
    await create_compliance(data, current_user=_user())
    insert_calls = [c for c in state["calls"] if c["mode"] == "insert"]
    assert "regulatory_body" not in insert_calls[0]["payload"]  # exclude_none, wasn't set


# ─── update_compliance ───────────────────────────────────────────────────────────────

async def test_update_only_sent_fields_and_recomputes_status_when_expiry_sent(patch_supabase):
    state = patch_supabase([{"id": 1, "equipment_name": "Crane", "expiry_date": _iso(100), "status": "current"}])
    result = await update_compliance(1, ComplianceUpdate(expiry_date=_iso(-1)), current_user=_user())
    assert result["status"] == "overdue"
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert update_calls[0]["payload"]["expiry_date"] == _iso(-1)
    assert update_calls[0]["payload"]["status"] == "overdue"


async def test_update_without_expiry_does_not_touch_status(patch_supabase):
    state = patch_supabase([{"id": 1, "equipment_name": "Crane", "expiry_date": _iso(100), "status": "current"}])
    await update_compliance(1, ComplianceUpdate(inspector="J. Doe"), current_user=_user())
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert "status" not in update_calls[0]["payload"]


async def test_update_explicit_null_clears_field(patch_supabase):
    state = patch_supabase([{"id": 1, "equipment_name": "Crane", "document_url": "http://x", "expiry_date": _iso(100)}])
    await update_compliance(1, ComplianceUpdate(document_url=None), current_user=_user())
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert update_calls[0]["payload"]["document_url"] is None


async def test_update_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await update_compliance(999, ComplianceUpdate(inspector="X"), current_user=_user())
    assert exc.value.status_code == 404


# ─── delete_compliance ───────────────────────────────────────────────────────────────

async def test_delete_happy_path(patch_supabase):
    state = patch_supabase([{"id": 1, "equipment_name": "Crane"}])
    result = await delete_compliance(1, current_user=_manager())
    assert result == {"ok": True}
    assert state["compliance_register"] == []
