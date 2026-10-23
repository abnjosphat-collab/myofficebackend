# tests/test_crud_scope.py — section-limit enforcement in the generic layer.
#
# CrudRouter routers opt in with scope_table=: lists gain the scope predicate
# (member-list tables filter in Python), creates stamp/refuse, and PATCH/DELETE
# check visibility BEFORE writing (missing or out-of-scope → 404, nothing
# written). Routers without scope_table= serve shared rows to every viewer,
# exactly as before scoping existed.

import importlib
from types import SimpleNamespace
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel


class LeaveCreate(BaseModel):
    employee_id: str
    status: Optional[str] = None


class LeaveUpdate(BaseModel):
    employee_id: Optional[str] = None
    status: Optional[str] = None


class RotationCreate(BaseModel):
    name: str
    members: list


class RotationUpdate(BaseModel):
    name: Optional[str] = None
    members: Optional[list] = None


class SpareCreate(BaseModel):
    name: str


class SpareUpdate(BaseModel):
    name: Optional[str] = None


class _Query:
    """Chainable fake honouring eq/in_ filters, with real insert/update/delete."""

    def __init__(self, table, store, calls):
        self._table = table
        self._store = store
        self._calls = calls
        self._rows = list(store[table])
        self._start = 0
        self._end = None
        self._pending_update = None
        self._deleting = False

    def select(self, *a, **k):
        self._calls.append(("select", a))
        return self

    def eq(self, col, val):
        self._calls.append(("eq", col, val))
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def in_(self, col, vals):
        self._calls.append(("in", col, list(vals)))
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self

    def or_(self, s):
        self._calls.append(("or_", s))
        return self

    def order(self, col, desc=False):
        self._calls.append(("order", col, desc))
        self._rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=bool(desc))
        return self

    def limit(self, n):
        self._calls.append(("limit", n))
        self._end = self._start + n
        return self

    def offset(self, n):
        self._calls.append(("offset", n))
        self._start = n
        return self

    def range(self, start, end):
        self._calls.append(("range", start, end))
        self._start, self._end = start, end + 1
        return self

    def insert(self, payload):
        self._calls.append(("insert", dict(payload)))
        new_id = max([r.get("id", 0) for r in self._store[self._table]] or [0]) + 1
        row = {"id": new_id, **payload}
        self._store[self._table].append(row)
        self._rows = [row]
        return self

    def update(self, payload):
        self._calls.append(("update", dict(payload)))
        self._pending_update = payload
        return self

    def delete(self):
        self._calls.append(("delete",))
        self._deleting = True
        return self

    def execute(self):
        if self._pending_update is not None:
            for r in self._rows:
                r.update(self._pending_update)
        if self._deleting:
            doomed = {id(r) for r in self._rows}
            self._store[self._table] = [r for r in self._store[self._table] if id(r) not in doomed]
            self._rows = []
        rows = self._rows[self._start:self._end]
        return SimpleNamespace(data=[dict(r) for r in rows])


class _FakeSupabase:
    def __init__(self, store, calls):
        self._store = store
        self._calls = calls

    def table(self, name):
        return _Query(name, self._store, self._calls)


def _store():
    return {
        "employees": [
            {"id": 1, "employee_id": "C1001", "section": "Projects"},
            {"id": 2, "employee_id": "C2001", "section": "Mechanical"},
        ],
        "leaves": [
            {"id": 11, "employee_id": "C1001", "status": "pending"},
            {"id": 12, "employee_id": "C2001", "status": "pending"},
        ],
        "duty_rotations": [
            {"id": 21, "name": "R1", "members": [{"employee_id": "C1001"}]},
            {"id": 22, "name": "R2", "members": [{"employee_id": "C2001"}]},
            {"id": 23, "name": "R3", "members": [{"employee_id": "C1001"}, {"employee_id": "C2001"}]},
        ],
        "spares": [{"id": 31, "name": "Bolt"}],
    }


SCOPED = {"user_id": "u-1", "role": "manager", "scope": "Projects"}
UNSCOPED = {"user_id": "u-9", "role": "manager", "scope": None}


@pytest.fixture
def scoped_client(monkeypatch):
    import app.crud_router as cr
    importlib.reload(cr)

    store, calls = _store(), []
    current = [UNSCOPED]
    monkeypatch.setattr(cr, "supabase", _FakeSupabase(store, calls))
    monkeypatch.setattr(cr, "get_current_user", lambda: current[0])
    monkeypatch.setattr(cr, "require_role", lambda role: (lambda: current[0]))

    app = FastAPI()
    app.include_router(cr.CrudRouter(
        "leaves", LeaveCreate, LeaveUpdate, scope_table="leaves").router, prefix="/api/leaves")
    app.include_router(cr.CrudRouter(
        "duty_rotations", RotationCreate, RotationUpdate, scope_table="duty_rotations").router,
        prefix="/api/duty-rotations")
    app.include_router(cr.CrudRouter(
        "spares", SpareCreate, SpareUpdate).router, prefix="/api/spares")
    client = TestClient(app, raise_server_exceptions=False)
    client.as_user = lambda user: current.__setitem__(0, user)  # noqa: E731
    client.scope_store = store
    client.scope_calls = calls
    return client


def _predicates(client):
    return [c for c in client.scope_calls if c[0] in ("eq", "in")]


# ─── lists ─────────────────────────────────────────────────────────────────

def test_scoped_person_list_is_predicated_in_the_query(scoped_client):
    scoped_client.as_user(SCOPED)
    resp = scoped_client.get("/api/leaves")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [11]
    assert ("in", "employee_id", ["C1001"]) in _predicates(scoped_client)


def test_unscoped_person_list_query_is_unchanged(scoped_client):
    scoped_client.as_user(UNSCOPED)
    resp = scoped_client.get("/api/leaves")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [11, 12]
    assert _predicates(scoped_client) == []


def test_scoped_members_list_needs_any_member_in_scope(scoped_client):
    scoped_client.as_user(SCOPED)
    resp = scoped_client.get("/api/duty-rotations")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [21, 23]


def test_scoped_members_list_honours_limit_offset_on_the_slice(scoped_client):
    scoped_client.as_user(SCOPED)
    assert [r["id"] for r in scoped_client.get("/api/duty-rotations?limit=1").json()] == [21]
    assert [r["id"] for r in scoped_client.get("/api/duty-rotations?offset=1").json()] == [23]
    assert [r["id"] for r in scoped_client.get("/api/duty-rotations?limit=1&offset=1").json()] == [23]


def test_exempt_table_serves_everyone_the_same_rows(scoped_client):
    scoped_client.as_user(SCOPED)
    assert scoped_client.get("/api/spares").json() == [{"id": 31, "name": "Bolt"}]
    scoped_client.as_user(UNSCOPED)
    assert scoped_client.get("/api/spares").json() == [{"id": 31, "name": "Bolt"}]


# ─── writes ────────────────────────────────────────────────────────────────

def test_scoped_create_for_out_of_scope_person_is_404_and_writes_nothing(scoped_client):
    scoped_client.as_user(SCOPED)
    resp = scoped_client.post("/api/leaves", json={"employee_id": "C2001"})
    assert resp.status_code == 404
    assert len(scoped_client.scope_store["leaves"]) == 2


def test_scoped_create_for_in_scope_person_passes(scoped_client):
    scoped_client.as_user(SCOPED)
    resp = scoped_client.post("/api/leaves", json={"employee_id": "C1001", "status": "pending"})
    assert resp.status_code == 200
    assert resp.json()["employee_id"] == "C1001"


def test_scoped_create_for_invisible_rotation_is_404(scoped_client):
    scoped_client.as_user(SCOPED)
    resp = scoped_client.post("/api/duty-rotations",
                              json={"name": "R9", "members": [{"employee_id": "C2001"}]})
    assert resp.status_code == 404
    assert len(scoped_client.scope_store["duty_rotations"]) == 3


def test_scoped_patch_of_out_of_scope_row_is_404(scoped_client):
    scoped_client.as_user(SCOPED)
    resp = scoped_client.patch("/api/leaves/12", json={"status": "approved"})
    assert resp.status_code == 404
    assert scoped_client.scope_store["leaves"][1]["status"] == "pending"


def test_scoped_patch_moving_a_row_out_of_scope_writes_nothing(scoped_client):
    scoped_client.as_user(SCOPED)
    resp = scoped_client.patch("/api/leaves/11", json={"employee_id": "C2001"})
    assert resp.status_code == 404
    assert scoped_client.scope_store["leaves"][0]["employee_id"] == "C1001"


def test_scoped_patch_within_scope_passes(scoped_client):
    scoped_client.as_user(SCOPED)
    resp = scoped_client.patch("/api/leaves/11", json={"status": "approved"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_scoped_delete_of_out_of_scope_row_is_404_and_keeps_it(scoped_client):
    scoped_client.as_user(SCOPED)
    resp = scoped_client.delete("/api/leaves/12")
    assert resp.status_code == 404
    assert len(scoped_client.scope_store["leaves"]) == 2


def test_unscoped_patch_delete_keep_blind_write_semantics(scoped_client):
    scoped_client.as_user(UNSCOPED)
    assert scoped_client.patch("/api/leaves/12", json={"status": "approved"}).status_code == 200
    assert scoped_client.delete("/api/leaves/12").status_code == 200
    assert scoped_client.delete("/api/leaves/999").status_code == 200  # missing still ok


def test_unknown_scope_table_fails_at_construction():
    import app.crud_router as cr
    import pytest as _pytest

    with _pytest.raises(RuntimeError):
        cr.CrudRouter("widgets", LeaveCreate, LeaveUpdate, scope_table="nope")
