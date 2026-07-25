# tests/test_bulk_rate_limits.py — the expensive bulk/import endpoints (spares bulk
# create, employees bulk-discipline, compressors CSV import) get a tighter 10/minute
# limit on top of the global 300/minute default (see app/rate_limit.py's module
# docstring, which described this but it was never actually wired up until now).
# Confirms the decorator is live and actually returns 429 once exceeded — not just
# that it's present in source.

import pytest
from fastapi.testclient import TestClient

from main import app
from app.auth import get_current_user
from app.rate_limit import limiter

client = TestClient(app)


@pytest.fixture
def bypass_auth():
    # The rate-limit decorator only runs once the endpoint body starts executing,
    # which is after FastAPI resolves Depends(get_current_user) — an unauthenticated
    # call 401s before ever reaching the limiter, so a real (or overridden) auth pass
    # is required to actually exercise it.
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u-test", "email": "t@t.com", "role": "user"}
    yield
    app.dependency_overrides.pop(get_current_user, None)
    # The limiter's storage is a single shared, process-global in-memory store keyed
    # by client IP — TestClient always presents the same fake IP, so without resetting,
    # deliberately exhausting the bucket here would leak into every other test file
    # that legitimately calls this same endpoint later in the same pytest run.
    limiter.reset()


def test_bulk_discipline_rate_limited_after_10_requests(bypass_auth):
    # ids=[] short-circuits to a 200 with no DB call (see bulk_set_discipline) — a safe,
    # side-effect-free payload for firing repeatedly against the real app.
    statuses = [client.post("/api/employees/bulk-discipline", json={"ids": [], "discipline": None}).status_code for _ in range(15)]
    assert 429 in statuses, f"expected a 429 among {statuses}"
    assert statuses[:10] == [200] * 10, f"first 10 requests should all succeed: {statuses}"
