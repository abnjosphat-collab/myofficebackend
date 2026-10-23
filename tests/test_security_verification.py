# tests/test_security_verification.py — the "signed-in smoke test" for the read guards +
# the auth 503 fix, done WITHOUT a real login/MFA:
#  * FastAPI dependency_overrides simulates an authenticated user, so we can prove the PII
#    read guards let a signed-in caller THROUGH (not just that they block anonymous ones).
#  * The auth 503 path is exercised with a mocked Supabase whose role RPC raises.
#
# Boots the full app via TestClient (no `with`, so no Redis lifespan needed). The
# authenticated reads DO hit the real Supabase (read-only) — we assert on status only.

import pytest
from fastapi.testclient import TestClient

from main import app
from app.auth import get_current_user
import app.auth as auth

client = TestClient(app)

PII_READS = [
    "/api/employees",
    "/api/employees/1",
    "/api/timesheets",
    "/api/leaves",
    "/api/leaves/1",
    "/api/overtime",
]


def _fake_authed_user():
    return {"user_id": "verify-1", "email": "verify@test.local", "role": "manager"}


@pytest.mark.parametrize("path", PII_READS)
def test_pii_read_blocks_anonymous(path):
    """No token → the guard blocks with 401 (the exposure we closed)."""
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", PII_READS)
def test_pii_read_allows_authenticated(path):
    """Signed-in user (simulated via dependency override) is NOT blocked — the guard lets
    them reach the handler. This is the 'does signed-in still work' check."""
    app.dependency_overrides[get_current_user] = _fake_authed_user
    try:
        r = client.get(path)
        assert r.status_code not in (401, 403), (
            f"{path} blocked an authenticated user ({r.status_code}) — the guard is over-applied"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


COMMERCIAL_READS = [
    "/api/equipment",
    "/api/equipment/",
    "/api/availabilities",
]


@pytest.mark.parametrize("path", COMMERCIAL_READS)
def test_commercial_read_blocks_anonymous(path):
    """No token -> 401. F-01: the equipment list and the merged availabilities
    list carry purchase/supplier/contact fields — signed-in users only
    (owner decision, 22 Oct 2026)."""
    assert client.get(path).status_code == 401


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    """Chainable single-table fake; execute() returns fresh copies (the
    availabilities merge writes its keys onto the equipment rows)."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return _Resp([dict(r) for r in self._rows])


class _FakeDB:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _Query(self._tables.get(name, []))


class _BrokenRedis:
    async def get(self, *a, **k):
        raise ConnectionError("no redis in tests")

    async def set(self, *a, **k):
        raise ConnectionError("no redis in tests")

    async def sadd(self, *a, **k):
        raise ConnectionError("no redis in tests")

    async def smembers(self, *a, **k):
        raise ConnectionError("no redis in tests")

    async def delete(self, *a, **k):
        raise ConnectionError("no redis in tests")


_FULL_ROW = {
    "id": 7, "equipment_id": "EQ-7", "name": "Pump 7",
    "purchase_price": 1200.5, "purchase_cost": 1100.0, "supplier": "Acme",
    "supplier_contact": "S. Upplier", "supplier_phone": "07700 900077",
    "serial_number": "SN-007", "assigned_to": "A. N. Other",
    "warranty_info": "2 years", "status": "operational",
}


def test_equipment_list_returns_full_row_to_signed_in_user(monkeypatch):
    """F-01 closed the list to signed-out callers; signed-in callers keep every
    field (owner decision: no projection)."""
    import app.routers.equipment as eq_mod
    import app.cache as cache_mod

    monkeypatch.setattr(eq_mod, "supabase", _FakeDB({"equipment": [_FULL_ROW]}))
    monkeypatch.setattr(cache_mod, "redis_client", _BrokenRedis())
    app.dependency_overrides[get_current_user] = _fake_authed_user
    try:
        r = client.get("/api/equipment")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert r.status_code == 200
    row = r.json()[0]
    for key in ("purchase_price", "purchase_cost", "supplier", "supplier_contact",
                "supplier_phone", "serial_number", "assigned_to", "warranty_info"):
        assert row[key] == _FULL_ROW[key]


def test_availabilities_returns_full_row_to_signed_in_user(monkeypatch):
    """Same guarantee for the merged list (no availability history here, so the
    merge fills its nulls and the equipment keys pass through untouched)."""
    import app.routers.availability as av_mod

    monkeypatch.setattr(av_mod, "supabase", _FakeDB({"equipment": [_FULL_ROW], "availabilities": []}))
    app.dependency_overrides[get_current_user] = _fake_authed_user
    try:
        r = client.get("/api/availabilities")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert r.status_code == 200
    row = r.json()[0]
    assert row["availability"] is None
    for key in ("purchase_price", "supplier", "supplier_phone", "serial_number", "assigned_to"):
        assert row[key] == _FULL_ROW[key]


def test_open_reads_stay_open_for_anonymous():
    """Aggregate stats + health must remain readable without a token (not swept up)."""
    for path in ["/api/leaves/stats/summary", "/api/employees/health/status"]:
        assert client.get(path).status_code != 401


# ─── auth 503 fix: role-lookup failure must fail loud, not silently downgrade ─────

class _FakeUser:
    id = "u-1"
    email = "x@y.com"
    factors = []  # real get_user populates this; [] = not MFA-enrolled


class _FakeAuthOK:
    def get_user(self, token):
        class R:
            user = _FakeUser()
        return R()


class _FakeSupabaseRoleRpcFails:
    auth = _FakeAuthOK()

    def rpc(self, *a, **k):
        return self

    def execute(self):
        raise RuntimeError("role RPC unavailable")


class _FakeSupabaseRoleRpcEmpty:
    auth = _FakeAuthOK()

    def rpc(self, *a, **k):
        return self

    def execute(self):
        class R:
            data = None       # succeeded, but no role assigned
        return R()


async def test_role_lookup_failure_raises_503(monkeypatch):
    """A valid token but a FAILING role RPC now raises a retryable 503 (was: silent 'user')."""
    monkeypatch.setattr(auth, "supabase", _FakeSupabaseRoleRpcFails())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user("Bearer good-token")
    assert exc.value.status_code == 503


async def test_role_lookup_empty_still_defaults_to_user(monkeypatch):
    """A SUCCESSFUL lookup with no role is not a failure — 'user' is the right default."""
    monkeypatch.setattr(auth, "supabase", _FakeSupabaseRoleRpcEmpty())
    result = await auth.get_current_user("Bearer good-token")
    assert result["role"] == "user"
