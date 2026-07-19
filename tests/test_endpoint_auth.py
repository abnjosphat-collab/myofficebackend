# tests/test_endpoint_auth.py — end-to-end check that the auth dependency is actually
# wired onto real routes (not just unit-tested in isolation). Boots the full app via
# FastAPI's TestClient and confirms representative write/delete endpoints reject an
# unauthenticated request with 401, while a representative read stays open.
#
# TestClient is used WITHOUT a `with` block on purpose: that skips the app's lifespan
# (which pings Redis with a 5s timeout), so these tests need no Redis and no live server.
# Auth is checked before any DB access, so no real database is needed either.

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


# (method, path) pairs across several different routers — a spread, not exhaustive.
PROTECTED_WRITES = [
    ("post", "/api/employees"),
    ("delete", "/api/employees/1"),
    ("post", "/api/equipment"),
    ("delete", "/api/equipment/1"),
    ("post", "/api/drivers"),
    ("post", "/api/maintenance/work-orders"),
    ("post", "/api/breakdowns/"),
    # leaves / overtime writes were unauthenticated until 2026-07-19 — a no-token
    # DELETE reached the handler and would have removed the row. Guard added.
    ("post", "/api/leaves"),
    ("patch", "/api/leaves/1"),
    ("delete", "/api/leaves/1"),
    ("post", "/api/overtime"),
    ("patch", "/api/overtime/1"),
    ("delete", "/api/overtime/1"),
    # Routers migrated onto the shared CrudRouter (2026-07-19) — auth must survive the
    # move from hand-written handlers to the base class, incl. its bespoke extensions.
    ("post", "/api/competency"),
    ("delete", "/api/competency/1"),
    ("post", "/api/failure-modes"),
    ("patch", "/api/condition-monitoring/1"),
    ("post", "/api/production"),
    ("delete", "/api/production/1"),
    ("post", "/api/contractors"),
    ("delete", "/api/contractors/1"),
    ("post", "/api/contractors/jobs"),      # sub-resource added alongside the base
    ("delete", "/api/contractors/jobs/1"),
    ("get", "/api/admin/users"),          # admin read is deliberately protected
    ("patch", "/api/admin/users/abc"),
]


@pytest.mark.parametrize("method,path", PROTECTED_WRITES)
def test_write_endpoints_require_auth(method, path):
    # httpx's get()/delete() don't accept a json body — only send one for methods
    # that take a request body.
    kwargs = {"json": {}} if method in ("post", "put", "patch") else {}
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 401, f"{method.upper()} {path} returned {resp.status_code}, expected 401"


OPEN_READS = [
    "/api/health",
    "/api/employees",
    "/api/equipment",
]


@pytest.mark.parametrize("path", OPEN_READS)
def test_read_endpoints_stay_open(path):
    # Reads are intentionally left unauthenticated — they must NOT 401. (They may 200 or
    # 500 depending on DB availability in the test env; the point is only that auth
    # isn't blocking them.)
    resp = client.get(path)
    assert resp.status_code != 401, f"GET {path} unexpectedly required auth ({resp.status_code})"


def test_invalid_token_is_rejected():
    resp = client.post("/api/employees", headers={"Authorization": "Bearer not-a-real-token"}, json={})
    assert resp.status_code == 401
