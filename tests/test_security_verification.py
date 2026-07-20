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


def test_open_reads_stay_open_for_anonymous():
    """Aggregate stats + health must remain readable without a token (not swept up)."""
    for path in ["/api/leaves/stats/summary", "/api/employees/health/status", "/api/equipment"]:
        assert client.get(path).status_code != 401


# ─── auth 503 fix: role-lookup failure must fail loud, not silently downgrade ─────

class _FakeUser:
    id = "u-1"
    email = "x@y.com"


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
