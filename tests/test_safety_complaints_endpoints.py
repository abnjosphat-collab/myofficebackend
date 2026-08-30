# tests/test_safety_complaints_endpoints.py — covers safety_complaints.py's
# handlers that test_safety_complaints_mapping.py doesn't: list_complaints
# (filters + client-side search), get_complaint, create_complaint (incl. the
# ""->None date-column coercion), update_complaint (incl. the same coercion
# and the "no fields" 400), delete_complaint, and the Pydantic validators on
# SafetyComplaintBase (priority/status normalization + rejection). Same
# "call the route coroutine directly against a fake supabase client" recipe.
#
# list_complaints' parameters are all FastAPI Query(...)-defaulted, so per the
# established gotcha every one of them must be passed explicitly when calling
# the coroutine directly — an omitted arg would bind to the Query() sentinel
# object instead of its real default.

import pytest
from pydantic import ValidationError
from fastapi import HTTPException

import app.routers.safety_complaints as sc_mod
from app.routers.safety_complaints import (
    SafetyComplaintCreate, SafetyComplaintUpdate,
    list_complaints, get_complaint, create_complaint, update_complaint, delete_complaint,
)


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state, response_map):
        self.table_name = table_name
        self.state = state
        self.response_map = response_map
        self._payload = None
        self._op = "select"
        self._single = False

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def range(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def single(self):
        self._single = True
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        self.state.setdefault("calls", []).append({"table": self.table_name, "op": self._op, "payload": self._payload})
        cfg = self.response_map.get(self.table_name, {})
        if callable(cfg):
            return _Resp(cfg(self._op, self._payload))
        if self._op == "update":
            return _Resp(cfg.get("update_return", []))
        if self._op == "insert":
            return _Resp(cfg.get("insert_return", []))
        if self._op == "delete":
            return _Resp(cfg.get("delete_return", []))
        if self._single:
            return _Resp(cfg.get("single_return"))
        return _Resp(cfg.get("select_return", []))


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
        monkeypatch.setattr(sc_mod, "supabase", _FakeSupabase(state, response_map))
        return state
    return _patch


LIST_DEFAULTS = dict(search=None, status=None, section=None, priority=None, category=None,
                      from_date=None, to_date=None, limit=200, offset=0)


# ─── list_complaints ─────────────────────────────────────────────────────────

async def test_list_complaints_returns_mapped_rows(patch_supabase):
    rows = [{"id": "1", "issue_raised": "Loose railing", "raised_by": "J"}]
    patch_supabase({"safety_complaints": {"select_return": rows}})
    result = await list_complaints(**LIST_DEFAULTS)
    assert result == [{
        "id": "1", "date": None, "raisedBy": "J", "issueRaised": "Loose railing",
        "category": "General", "priority": "medium", "section": "General", "location": "",
        "actionPlan": "", "byWho": "", "byWhen": "", "supervisorName": "", "supervisorSignature": "",
        "dateClosed": None, "status": "open", "submittedAt": None,
    }]


async def test_list_complaints_applies_all_filters_without_error(patch_supabase):
    state = patch_supabase({"safety_complaints": {"select_return": []}})
    result = await list_complaints(
        search=None, status="open", section="Yard", priority="high", category="Housekeeping",
        from_date="2024-01-01", to_date="2024-01-31", limit=50, offset=10,
    )
    assert result == []
    assert state["calls"][0]["table"] == "safety_complaints"


async def test_list_complaints_search_matches_across_multiple_fields(patch_supabase):
    rows = [
        {"id": "1", "issue_raised": "Loose railing near dock", "raised_by": "Alice"},
        {"id": "2", "issue_raised": "Spill in aisle 3", "raised_by": "Bob", "location": "Aisle 3"},
        {"id": "3", "issue_raised": "Unrelated", "raised_by": "Carol"},
    ]
    patch_supabase({"safety_complaints": {"select_return": rows}})
    result = await list_complaints(**{**LIST_DEFAULTS, "search": "aisle"})
    assert {r["id"] for r in result} == {"2"}


async def test_list_complaints_raises_500_on_error(patch_supabase):
    def raiser(op, payload):
        raise RuntimeError("db down")
    patch_supabase({"safety_complaints": raiser})
    with pytest.raises(HTTPException) as exc:
        await list_complaints(**LIST_DEFAULTS)
    assert exc.value.status_code == 500


# ─── get_complaint ────────────────────────────────────────────────────────────

async def test_get_complaint_returns_mapped_row(patch_supabase):
    patch_supabase({"safety_complaints": {"single_return": {"id": "1", "issue_raised": "X"}}})
    result = await get_complaint("1")
    assert result["issueRaised"] == "X"


async def test_get_complaint_404_when_missing(patch_supabase):
    patch_supabase({"safety_complaints": {"single_return": None}})
    with pytest.raises(HTTPException) as exc:
        await get_complaint("ghost")
    assert exc.value.status_code == 404


async def test_get_complaint_raises_500_on_error(patch_supabase):
    def raiser(op, payload):
        raise RuntimeError("boom")
    patch_supabase({"safety_complaints": raiser})
    with pytest.raises(HTTPException) as exc:
        await get_complaint("1")
    assert exc.value.status_code == 500


# ─── create_complaint ─────────────────────────────────────────────────────────

async def test_create_complaint_inserts_and_returns_mapped_row(patch_supabase):
    state = patch_supabase({"safety_complaints": {"insert_return": [{"id": "new1", "issue_raised": "Fire exit blocked"}]}})
    payload = SafetyComplaintCreate(date="2024-01-01", issue_raised="Fire exit blocked")
    result = await create_complaint(payload, current_user={"user_id": "u1"})
    assert result["issueRaised"] == "Fire exit blocked"
    inserted = state["calls"][0]["payload"]
    assert inserted["id"] is not None
    assert inserted["by_when"] is None       # blank -> None for the date column
    assert inserted["date_closed"] is None


async def test_create_complaint_raises_500_when_insert_returns_nothing(patch_supabase):
    patch_supabase({"safety_complaints": {"insert_return": []}})
    payload = SafetyComplaintCreate(date="2024-01-01", issue_raised="Fire exit blocked")
    with pytest.raises(HTTPException) as exc:
        await create_complaint(payload, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_create_complaint_raises_500_on_error(patch_supabase):
    def raiser(op, payload):
        raise RuntimeError("boom")
    patch_supabase({"safety_complaints": raiser})
    payload = SafetyComplaintCreate(date="2024-01-01", issue_raised="Fire exit blocked")
    with pytest.raises(HTTPException) as exc:
        await create_complaint(payload, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── update_complaint ─────────────────────────────────────────────────────────

async def test_update_complaint_coerces_blank_date_fields_to_none(patch_supabase):
    state = patch_supabase({"safety_complaints": {"update_return": [{"id": "1", "status": "closed"}]}})
    result = await update_complaint("1", SafetyComplaintUpdate(status="closed", by_when=""), current_user={"user_id": "u1"})
    assert result["status"] == "closed"
    payload = state["calls"][0]["payload"]
    assert payload["by_when"] is None
    assert payload["status"] == "closed"


async def test_update_complaint_no_fields_is_400(patch_supabase):
    state = patch_supabase({"safety_complaints": {}})
    with pytest.raises(HTTPException) as exc:
        await update_complaint("1", SafetyComplaintUpdate(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 400
    assert state["calls"] == []   # rejected before any query


async def test_update_complaint_404_when_missing(patch_supabase):
    patch_supabase({"safety_complaints": {"update_return": []}})
    with pytest.raises(HTTPException) as exc:
        await update_complaint("ghost", SafetyComplaintUpdate(status="closed"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 404


async def test_update_complaint_raises_500_on_error(patch_supabase):
    def raiser(op, payload):
        raise RuntimeError("boom")
    patch_supabase({"safety_complaints": raiser})
    with pytest.raises(HTTPException) as exc:
        await update_complaint("1", SafetyComplaintUpdate(status="closed"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── delete_complaint ─────────────────────────────────────────────────────────

async def test_delete_complaint_success(patch_supabase):
    patch_supabase({"safety_complaints": {"delete_return": [{"id": "1"}]}})
    await delete_complaint("1", current_user={"user_id": "u1", "role": "manager"})  # no raise


async def test_delete_complaint_404_when_missing(patch_supabase):
    patch_supabase({"safety_complaints": {"delete_return": []}})
    with pytest.raises(HTTPException) as exc:
        await delete_complaint("ghost", current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 404


async def test_delete_complaint_raises_500_on_error(patch_supabase):
    def raiser(op, payload):
        raise RuntimeError("boom")
    patch_supabase({"safety_complaints": raiser})
    with pytest.raises(HTTPException) as exc:
        await delete_complaint("1", current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 500


# ─── Pydantic validators (priority/status) ────────────────────────────────────

def test_priority_is_lowercased():
    m = SafetyComplaintCreate(date="2024-01-01", issue_raised="Something wrong", priority="HIGH")
    assert m.priority == "high"


def test_priority_rejects_unknown_value():
    with pytest.raises(ValidationError):
        SafetyComplaintCreate(date="2024-01-01", issue_raised="Something wrong", priority="urgent")


def test_status_is_lowercased():
    m = SafetyComplaintCreate(date="2024-01-01", issue_raised="Something wrong", status="CLOSED")
    assert m.status == "closed"


def test_status_rejects_unknown_value():
    with pytest.raises(ValidationError):
        SafetyComplaintCreate(date="2024-01-01", issue_raised="Something wrong", status="pending")


def test_issue_raised_too_short_is_rejected():
    with pytest.raises(ValidationError):
        SafetyComplaintCreate(date="2024-01-01", issue_raised="hi")
