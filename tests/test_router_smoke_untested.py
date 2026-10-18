# tests/test_router_smoke_untested.py — smoke tests for the 8 routers that had no
# test file: the auth gate (full app, no token -> 401) plus one happy-path list
# read (mounted router + FakeSupabase + neutered auth). The CrudRouter machinery
# itself is proven by test_crud_router.py; this pins each router's wiring.

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import FakeSupabase

import app.crud_router as cr_mod
from app.auth import get_current_user as real_get_current_user
from main import app as full_app


def _authed_user():
    return {"user_id": "u1", "role": "manager", "email": "t@t"}


def _mounted(mod_name, prefix, monkeypatch, rows):
    """The real router on a bare app, DB faked and auth neutered."""
    mod = importlib.import_module(f"app.routers.{mod_name}")
    store = {"queries": [], "data": rows}
    fake = FakeSupabase(store)
    # The supabase handle is a module global (looked up at call time), so a
    # monkeypatch rewires it. Auth dependencies are bound into each route at
    # registration, so they need dependency_overrides, not a module patch.
    monkeypatch.setattr(mod, "supabase", fake, raising=False)
    monkeypatch.setattr(cr_mod, "supabase", fake, raising=False)
    app = FastAPI()
    app.include_router(mod.router, prefix=prefix)
    app.dependency_overrides[real_get_current_user] = _authed_user
    return TestClient(app)


# module, mount prefix, (gate method, gate path), list rows, expected id in body
ROUTERS = [
    ("competency", "/api/competency",
     [("post", "/api/competency"), ("delete", "/api/competency/1")],
     [{"id": 1, "employee_id": "E1", "employee_name": "Ann", "equipment_type": "Mill", "skill_area": "Ops"}]),
    ("condition_monitoring", "/api/condition-monitoring",
     [("post", "/api/condition-monitoring"), ("delete", "/api/condition-monitoring/1")],
     [{"id": 1, "equipment_name": "Mill", "monitoring_type": "oil", "sampled_date": "2026-01-01"}]),
    ("failure_modes", "/api/failure-modes",
     [("post", "/api/failure-modes"), ("delete", "/api/failure-modes/1")],
     [{"id": 1, "equipment_type": "Mill", "component": "Bearing", "failure_mode": "Wear"}]),
    ("handover", "/api/handover",
     [("post", "/api/handover"), ("delete", "/api/handover/1")],
     [{"id": 1, "handover_date": "2026-01-01", "shift": "day", "outgoing_supervisor": "A"}]),
    ("job_cards", "/api/job-cards",
     [("post", "/api/job-cards"), ("delete", "/api/job-cards/1")],
     [{"id": 1, "job_no": "JC-1", "title": "Fix pump"}]),
    ("production", "/api/production",
     [("post", "/api/production"), ("delete", "/api/production/1")],
     [{"id": 1, "prod_date": "2026-01-01"}]),
    ("ppe_order_list", "/api/ppe-order-list",
     [("post", "/api/ppe-order-list"), ("delete", "/api/ppe-order-list")],
     [{"record_id": "R1", "entry": {"item_name": "Gloves"}, "added_at": "2026-01-01"}]),
    ("saved_quotations", "/api/quotations",
     [("put", "/api/quotations/QT-1"), ("delete", "/api/quotations/QT-1")],
     [{"id": "QT-1", "saved_at": "2026-01-01", "saved_by": "a@b", "draft": {"client": "X"}}]),
]


@pytest.mark.parametrize("mod_name,prefix,gates,rows", ROUTERS, ids=[r[0] for r in ROUTERS])
def test_write_without_token_is_401(mod_name, prefix, gates, rows):
    client = TestClient(full_app)
    for method, path in gates:
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, f"{method.upper()} {path} -> {resp.status_code}"


@pytest.mark.parametrize("mod_name,prefix,gates,rows", ROUTERS, ids=[r[0] for r in ROUTERS])
def test_list_happy_path(mod_name, prefix, gates, rows, monkeypatch):
    client = _mounted(mod_name, prefix, monkeypatch, rows)
    resp = client.get(prefix + "/")
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    assert isinstance(body, list) and len(body) == len(rows)
