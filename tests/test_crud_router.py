# tests/test_crud_router.py — unit tests for the generic CrudRouter (app/crud_router.py).
#
# Uses a fake Supabase client that records the query chain, so we can assert the base
# builds exactly the query the hand-written routers used to (order column + direction,
# equality filters, limit) and passes data through unchanged — without touching a real DB.
# Auth dependencies are patched to no-ops here; the real auth gating is covered end-to-end
# in test_endpoint_auth.py.

import importlib
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Records every call and is fully chainable; execute() returns the canned rows."""
    def __init__(self, table, store):
        self.table_name = table
        self.store = store
        store["queries"].append(self)
        self.calls = []

    def select(self, *a, **k):     self.calls.append(("select", a));       return self
    def eq(self, col, val):        self.calls.append(("eq", col, val));    return self
    def or_(self, s):              self.calls.append(("or_", s));          return self
    def order(self, col, desc=False): self.calls.append(("order", col, desc)); return self
    def limit(self, n):            self.calls.append(("limit", n));        return self
    def offset(self, n):           self.calls.append(("offset", n));       return self
    def insert(self, d):           self.calls.append(("insert", d)); self.store["insert"] = d; return self
    def update(self, d):           self.calls.append(("update", d)); self.store["update"] = d; return self
    def delete(self):              self.calls.append(("delete",));         return self

    def execute(self):
        return FakeResult(self.store["data"])


class FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


class ItemCreate(BaseModel):
    name: str
    kind: Optional[str] = None
    note: Optional[str] = None


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    note: Optional[str] = None


@pytest.fixture
def client_and_store(monkeypatch):
    """Build a fresh app mounting one CrudRouter backed by the fake Supabase."""
    import app.crud_router as cr
    importlib.reload(cr)

    store = {"queries": [], "data": []}
    fake = FakeSupabase(store)
    # Patch the supabase handle the module already imported, and neutralise auth.
    monkeypatch.setattr(cr, "supabase", fake)
    monkeypatch.setattr(cr, "get_current_user", lambda: {"role": "manager", "email": "t@t"})
    monkeypatch.setattr(cr, "require_role", lambda role: (lambda: {"role": role}))

    crud = cr.CrudRouter(
        "widgets", ItemCreate, ItemUpdate,
        tags=["Widgets"], order_by="name", order_desc=False,
        filters={"kind": "kind"}, default_limit=None, not_found="Widget not found",
    )
    app = FastAPI()
    app.include_router(crud.router, prefix="/api/widgets")
    return TestClient(app), store


def _last_query(store):
    return store["queries"][-1]


def test_list_returns_rows_and_serializes(client_and_store):
    client, store = client_and_store
    store["data"] = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    resp = client.get("/api/widgets")
    assert resp.status_code == 200
    assert resp.json() == [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]


def test_list_applies_order_and_no_limit(client_and_store):
    client, store = client_and_store
    client.get("/api/widgets")
    calls = _last_query(store).calls
    assert ("order", "name", False) in calls
    assert not any(c[0] == "limit" for c in calls)  # default_limit=None → unbounded


def test_list_applies_eq_filter_only_when_present(client_and_store):
    client, store = client_and_store
    client.get("/api/widgets?kind=gadget")
    assert ("eq", "kind", "gadget") in _last_query(store).calls
    # empty value must NOT add a filter
    client.get("/api/widgets?kind=")
    assert not any(c[0] == "eq" for c in _last_query(store).calls)


def test_trailing_slash_is_equivalent(client_and_store):
    client, store = client_and_store
    assert client.get("/api/widgets").status_code == 200
    assert client.get("/api/widgets/").status_code == 200


def test_create_inserts_exclude_none(client_and_store):
    client, store = client_and_store
    store["data"] = [{"id": 5, "name": "New"}]
    resp = client.post("/api/widgets", json={"name": "New", "kind": None})
    assert resp.status_code == 200
    assert store["insert"] == {"name": "New"}  # kind=None dropped by exclude_none


def test_update_excludes_unset_keeps_explicit_null(client_and_store):
    client, store = client_and_store
    store["data"] = [{"id": 5, "name": "X", "note": None}]
    # note explicitly null must be SENT (clears field); kind unset must be dropped
    resp = client.patch("/api/widgets/5", json={"name": "X", "note": None})
    assert resp.status_code == 200
    assert store["update"] == {"name": "X", "note": None}
    assert "kind" not in store["update"]


def test_update_404_when_no_row(client_and_store):
    client, store = client_and_store
    store["data"] = []  # update returns nothing → not found
    resp = client.patch("/api/widgets/999", json={"name": "X"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Widget not found"


def test_delete_returns_ok(client_and_store):
    client, store = client_and_store
    resp = client.delete("/api/widgets/5")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_reject_empty_update_returns_400(monkeypatch):
    """With reject_empty_update=True, a PATCH resolving to no fields is a 400 (job_cards)."""
    import app.crud_router as cr
    importlib.reload(cr)
    store = {"queries": [], "data": [{"id": 1, "name": "X"}]}
    monkeypatch.setattr(cr, "supabase", FakeSupabase(store))
    monkeypatch.setattr(cr, "get_current_user", lambda: {"role": "manager"})
    monkeypatch.setattr(cr, "require_role", lambda role: (lambda: {"role": role}))
    crud = cr.CrudRouter("jc", ItemCreate, ItemUpdate, reject_empty_update=True)
    app = FastAPI()
    app.include_router(crud.router, prefix="/api/jc")
    client = TestClient(app)

    # empty patch → 400
    assert client.patch("/api/jc/1", json={}).status_code == 400
    # non-empty patch still works
    assert client.patch("/api/jc/1", json={"name": "Y"}).status_code == 200


def test_default_limit_is_honoured(monkeypatch):
    """A router configured with default_limit applies .limit() and honours ?limit=."""
    import app.crud_router as cr
    importlib.reload(cr)
    store = {"queries": [], "data": []}
    monkeypatch.setattr(cr, "supabase", FakeSupabase(store))
    monkeypatch.setattr(cr, "get_current_user", lambda: {"role": "manager"})
    monkeypatch.setattr(cr, "require_role", lambda role: (lambda: {"role": role}))
    crud = cr.CrudRouter("prod", ItemCreate, ItemUpdate, order_by="d", order_desc=True, default_limit=30)
    app = FastAPI()
    app.include_router(crud.router, prefix="/api/prod")
    client = TestClient(app)

    client.get("/api/prod")
    assert ("limit", 30) in store["queries"][-1].calls
    client.get("/api/prod?limit=5")
    assert ("limit", 5) in store["queries"][-1].calls
    assert ("order", "d", True) in store["queries"][-1].calls
