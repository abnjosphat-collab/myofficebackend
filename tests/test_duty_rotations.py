# tests/test_duty_rotations.py — HTTP-level tests for the CrudRouter-backed
# duty_rotations endpoint, using the shared fake from conftest.py (auth is
# neutralised here; real gating lives in test_endpoint_auth.py).
import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import FakeSupabase


def _member(**kw):
    row = {"employee_id": "C001", "employee_name": "Alice", "phone": "+263771234567"}
    row.update(kw)
    return row


def _body(**kw):
    body = {
        "name": "Mine-wide duty",
        "department": None,
        "members": [_member(), _member(employee_id="C002", employee_name="Bob")],
        "week_length_days": 7,
        "cycle_start_date": "2026-10-05",
    }
    body.update(kw)
    return body


@pytest.fixture
def client_and_store(monkeypatch):
    import app.crud_router as cr
    import app.routers.duty_rotations as rm
    importlib.reload(cr)

    store = {"queries": [], "data": []}
    fake = FakeSupabase(store)
    monkeypatch.setattr(cr, "supabase", fake)
    monkeypatch.setattr(cr, "get_current_user", lambda: {"user_id": "t", "role": "manager"})
    monkeypatch.setattr(cr, "require_role", lambda role: (lambda: {"role": role}))
    importlib.reload(rm)
    monkeypatch.setattr(rm, "supabase", fake)

    app = FastAPI()
    app.include_router(rm.router, prefix="/api/duty-rotations")
    app.dependency_overrides[rm.get_current_user] = lambda: {"user_id": "t", "role": "manager"}
    return TestClient(app), store


def _last_query(store):
    return store["queries"][-1]


def test_list_returns_rows_ordered_by_name(client_and_store):
    client, store = client_and_store
    store["data"] = [{"id": 1, "name": "B"}, {"id": 2, "name": "A"}]
    resp = client.get("/api/duty-rotations")
    assert resp.status_code == 200
    assert resp.json() == store["data"]
    assert ("order", "name", False) in _last_query(store).calls


def test_list_department_filter_and_search(client_and_store):
    client, store = client_and_store
    client.get("/api/duty-rotations?department=Engineering")
    assert ("eq", "department", "Engineering") in _last_query(store).calls
    client.get("/api/duty-rotations?search=eng")
    assert any(c[0] == "or_" for c in _last_query(store).calls)


def test_get_one_returns_row_or_404(client_and_store):
    client, store = client_and_store
    store["data"] = [{"id": 3, "name": "Mech"}]
    assert client.get("/api/duty-rotations/3").status_code == 200
    store["data"] = []
    assert client.get("/api/duty-rotations/9").status_code == 404


def test_create_inserts_members_and_defaults_week_to_7(client_and_store):
    client, store = client_and_store
    store["data"] = [{"id": 5, **_body()}]
    body = _body()
    del body["week_length_days"]
    resp = client.post("/api/duty-rotations", json=body)
    assert resp.status_code == 200
    insert = store["insert"]
    assert insert["week_length_days"] == 7
    assert insert["members"][1]["employee_name"] == "Bob"
    assert insert["is_active"] is True


def test_create_rejects_empty_members_bad_date_and_bad_week(client_and_store):
    client, store = client_and_store
    assert client.post("/api/duty-rotations", json=_body(members=[])).status_code == 422
    assert client.post("/api/duty-rotations", json=_body(cycle_start_date="05/10/2026")).status_code == 422
    assert client.post("/api/duty-rotations", json=_body(week_length_days=0)).status_code == 422
    assert client.post("/api/duty-rotations", json=_body(members=[{"employee_id": "C1"}])).status_code == 422


def test_update_sends_only_set_fields(client_and_store):
    client, store = client_and_store
    store["data"] = [{"id": 2, **_body(notes="hi")}]
    resp = client.patch("/api/duty-rotations/2", json={"notes": "hi"})
    assert resp.status_code == 200
    assert store["update"] == {"notes": "hi"}


def test_update_rejects_emptied_members_and_bad_week(client_and_store):
    client, store = client_and_store
    assert client.patch("/api/duty-rotations/2", json={"members": []}).status_code == 422
    assert client.patch("/api/duty-rotations/2", json={"week_length_days": 99}).status_code == 422


def test_update_missing_row_is_404(client_and_store):
    client, store = client_and_store
    store["data"] = []
    assert client.patch("/api/duty-rotations/9", json={"notes": "x"}).status_code == 404


def test_delete_returns_ok(client_and_store):
    client, store = client_and_store
    resp = client.delete("/api/duty-rotations/2")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_trailing_slash_is_equivalent(client_and_store):
    client, store = client_and_store
    assert client.get("/api/duty-rotations").status_code == 200
    assert client.get("/api/duty-rotations/").status_code == 200


def test_update_members_and_start_date_pass_through(client_and_store):
    client, store = client_and_store
    store["data"] = [{"id": 2, **_body()}]
    resp = client.patch("/api/duty-rotations/2", json={
        "members": [_member(employee_id="C009", employee_name="Zed")],
        "cycle_start_date": "2026-11-02",
    })
    assert resp.status_code == 200
    assert store["update"]["cycle_start_date"] == "2026-11-02"
    assert store["update"]["members"] == [_member(employee_id="C009", employee_name="Zed")]
    assert client.patch("/api/duty-rotations/2", json={"cycle_start_date": "not-a-date"}).status_code == 422
