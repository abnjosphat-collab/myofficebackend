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
    ("post", "/api/recently-deleted/abc/restore"),
    ("delete", "/api/employees/1"),
    ("post", "/api/equipment"),
    ("delete", "/api/equipment/1"),
    ("post", "/api/drivers"),
    ("post", "/api/maintenance/work-orders"),
    ("post", "/api/maintenance/work-orders/bulk-delete"),
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
    ("post", "/api/handover"),
    ("delete", "/api/handover/1"),
    ("post", "/api/job-cards"),
    ("delete", "/api/job-cards/1"),
    ("get", "/api/admin/users"),          # admin read is deliberately protected
    ("patch", "/api/admin/users/abc"),
    ("post", "/api/admin/users/invite"),
    ("patch", "/api/admin/users/abc/active"),
    ("post", "/api/admin/users/abc/reset-password"),
    # PPE matrix writes (interval save + recalculate) are manager-gated.
    ("put", "/api/ppe/matrix"),
    ("post", "/api/ppe/matrix/worksuit/apply"),
    # PII reads guarded 2026-07-19 — individual employee/timesheet/leave/overtime
    # records must not be readable without a token. (Aggregate /stats/summary stays open.)
    ("get", "/api/employees"),
    ("get", "/api/employees/1"),
    ("get", "/api/employees/search/foo"),
    ("get", "/api/timesheets"),
    ("get", "/api/timesheets/1"),
    ("get", "/api/leaves"),
    ("get", "/api/leaves/1"),
    ("get", "/api/overtime"),
    # Tasks & Events — the whole router is manager+-gated at mount time (main.py),
    # on top of each route's own get_current_user/require_role. No token at all
    # must still be 401 here (the 403-for-insufficient-role case is covered
    # separately in test_tasks_events_router.py, since it needs a
    # valid-but-low-role token).
    ("get", "/api/tasks-events"),
    ("post", "/api/tasks-events"),
    ("patch", "/api/tasks-events/1"),
    ("delete", "/api/tasks-events/1"),
    ("get", "/api/tasks-events/1/comments"),
    ("post", "/api/tasks-events/1/comments"),
]


@pytest.mark.parametrize("method,path", PROTECTED_WRITES)
def test_write_endpoints_require_auth(method, path):
    # httpx's get()/delete() don't accept a json body — only send one for methods
    # that take a request body.
    kwargs = {"json": {}} if method in ("post", "put", "patch") else {}
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 401, f"{method.upper()} {path} returned {resp.status_code}, expected 401"


# The 6 data-bearing open reads are DELIBERATE (see docs/ENGINEERING_STANDARDS.md,
# "Open reads: what and why") — aggregate/operational data the homepage and shared
# pick-lists need without a token — plus the health probes. Anything listed here
# returning 401 is a bug; any other data-bearing read NOT requiring auth is a bug.
OPEN_READS = [
    "/api/health",
    "/api/employees/health/status",   # health probe stays open
    "/api/availabilities/stats",      # aggregate counts
    "/api/breakdowns/dashboard/overview",  # homepage KPI aggregates
    "/api/leaves/stats/summary",      # aggregate counts, not individual records
    "/api/lookup-lists/location",     # shared pick-list values
    "/api/maintenance/work-orders/stats/summary",  # aggregate counts
    "/api/timesheets/stats/summary",  # aggregate counts, not individual records
]


@pytest.mark.parametrize("path", OPEN_READS)
def test_read_endpoints_stay_open(path):
    # Reads are intentionally left unauthenticated — they must NOT 401. (They may 200 or
    # 500 depending on DB availability in the test env; the point is only that auth
    # isn't blocking them.)
    resp = client.get(path)
    assert resp.status_code != 401, f"GET {path} unexpectedly required auth ({resp.status_code})"


def test_invalid_token_is_rejected(monkeypatch):
    class InvalidTokenResponse:
        user = None

    monkeypatch.setattr("app.auth.supabase.auth.get_user", lambda _token: InvalidTokenResponse())
    resp = client.post("/api/employees", headers={"Authorization": "Bearer not-a-real-token"}, json={})
    assert resp.status_code == 401


# Leftover dev scaffolding, unauthenticated, discovered 2026-07-30: each did a live
# select("*") and three (near_miss, overtime, sheq_inspections) also did a live INSERT
# — overtime's never cleaned up after itself, leaving a permanent "Debug Test"/
# "DEBUG001" row in production (id=981, created 2026-07-13). Removed entirely rather
# than just gated, since a GET with write side effects has no legitimate authenticated
# use either. This test guards against the pattern quietly coming back.
REMOVED_DEBUG_ENDPOINTS = [
    "/api/nearmiss/debug/test",
    "/api/overtime/debug/test",
    "/api/pachedu/debug/test",
    "/api/pto/debug/test",
    "/api/sheq/debug/test",
    "/api/vfl/debug/test",
    "/api/work-stoppage/debug/test",
]


# Liveness "is the router mounted" probes, deleted 2026-10: unauthenticated
# surface with no legitimate use (the /health routes cover liveness).
REMOVED_LIVENESS_PROBES = [
    "/api/compressors/test",
    "/api/nearmiss/test",
    "/api/requisitions/test",
    "/api/spares/test/connection",
]


@pytest.mark.parametrize("path", REMOVED_DEBUG_ENDPOINTS)
def test_debug_test_endpoints_removed(path):
    resp = client.get(path)
    assert resp.status_code == 404, f"GET {path} should be gone (404), got {resp.status_code}"


@pytest.mark.parametrize("path", REMOVED_LIVENESS_PROBES)
def test_liveness_probes_removed(path):
    # Gone means: 404 (no route), or 401 where the path is now shadowed by
    # that router's protected /{id} route (/api/nearmiss/test and
    # /api/requisitions/test hit the auth gate before any handler). Either
    # way the open debug payload is gone — a 200 here is the failure.
    resp = client.get(path)
    assert resp.status_code in (401, 404), f"GET {path} should be gone (401/404), got {resp.status_code}"
