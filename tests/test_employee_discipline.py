# tests/test_employee_discipline.py — the bulk mechanical/electrical discipline endpoint
# (app/routers/employees.py POST /bulk-discipline). Fake Supabase; no network.
#
# employees.py wires `Depends(get_current_user)` at decoration time (module import), unlike
# CrudRouter which builds routes programmatically — so patching app.routers.employees.
# get_current_user after the fact does nothing for these routes. FastAPI's
# app.dependency_overrides is the mechanism built for exactly this: it overrides by the
# dependency callable's IDENTITY, which is why we import get_current_user from app.auth
# (the same object the decorator captured) rather than off the employees module.

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.employees as emp
from app.auth import get_current_user


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        store["queries"].append(self)

    def update(self, data):
        self.store["last_update"] = data
        return self

    def in_(self, col, vals):
        self.store["last_in"] = (col, vals)
        return self

    def execute(self):
        return FakeResult([])


class FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


async def _noop_invalidate(ns):
    return None


@pytest.fixture
def client_and_store(monkeypatch):
    store = {"queries": []}
    # supabase / invalidate_namespace are looked up at CALL time inside the handler body,
    # so a plain monkeypatch on the module attribute is enough (no reload needed).
    monkeypatch.setattr(emp, "supabase", FakeSupabase(store))
    monkeypatch.setattr(emp, "invalidate_namespace", _noop_invalidate)

    test_app = FastAPI()
    test_app.include_router(emp.router, prefix="/api/employees")
    test_app.dependency_overrides[get_current_user] = lambda: {"role": "manager", "email": "t@t"}
    yield TestClient(test_app), store
    test_app.dependency_overrides.clear()


def test_bulk_set_mechanical(client_and_store):
    client, store = client_and_store
    resp = client.post("/api/employees/bulk-discipline", json={"ids": [1, 2, 3], "discipline": "mechanical"})
    assert resp.status_code == 200
    assert resp.json() == {"updated": 3, "discipline": "mechanical"}
    assert store["last_update"] == {"discipline": "mechanical"}
    assert store["last_in"] == ("id", [1, 2, 3])


def test_bulk_set_electrical(client_and_store):
    client, store = client_and_store
    resp = client.post("/api/employees/bulk-discipline", json={"ids": [5], "discipline": "electrical"})
    assert resp.status_code == 200
    assert resp.json()["discipline"] == "electrical"


def test_bulk_clear_discipline(client_and_store):
    client, store = client_and_store
    resp = client.post("/api/employees/bulk-discipline", json={"ids": [1], "discipline": None})
    assert resp.status_code == 200
    assert store["last_update"] == {"discipline": None}


def test_bulk_rejects_invalid_discipline(client_and_store):
    client, _ = client_and_store
    resp = client.post("/api/employees/bulk-discipline", json={"ids": [1], "discipline": "plumbing"})
    assert resp.status_code == 400


def test_bulk_empty_ids_is_a_noop(client_and_store):
    client, store = client_and_store
    resp = client.post("/api/employees/bulk-discipline", json={"ids": [], "discipline": "mechanical"})
    assert resp.status_code == 200
    assert resp.json() == {"updated": 0, "discipline": "mechanical"}
    assert store["queries"] == []  # never touched the DB


def test_bulk_discipline_requires_auth():
    """No dependency_overrides here — a real unauthenticated call must 401."""
    from main import app as real_app
    client = TestClient(real_app)
    resp = client.post("/api/employees/bulk-discipline", json={"ids": [1], "discipline": "mechanical"})
    assert resp.status_code == 401
