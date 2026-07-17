# tests/test_auth.py — the role-based access control in app/auth.py, the core of
# the whole authorization scheme (get_current_user + require_role, applied to 159+
# endpoints). Pure-logic + mocked-Supabase; no network, no live server.

import pytest
from fastapi import HTTPException

from app import auth


# ─── role_at_least (pure logic) ───────────────────────────────────────────────

def test_role_at_least_ordering():
    # Exact match and above pass; below fails.
    assert auth.role_at_least("manager", "manager") is True
    assert auth.role_at_least("admin", "manager") is True
    assert auth.role_at_least("super_admin", "viewer") is True
    assert auth.role_at_least("user", "manager") is False
    assert auth.role_at_least("viewer", "user") is False


def test_role_at_least_unknown_role_fails_closed():
    # An unrecognised role must be treated as insufficient, never crash.
    assert auth.role_at_least("wizard", "viewer") is False
    assert auth.role_at_least("manager", "overlord") is False


# ─── Fake Supabase so get_current_user runs without network ───────────────────

class _FakeAuth:
    def __init__(self, user):
        self._user = user

    def get_user(self, token):
        class R: pass
        r = R()
        r.user = self._user
        return r


class _FakeRpcResult:
    def __init__(self, data):
        self.data = data


class _FakeSupabase:
    def __init__(self, user, role):
        self.auth = _FakeAuth(user)
        self._role = role

    def rpc(self, name, params):
        self._last_rpc = (name, params)
        return self

    def execute(self):
        return _FakeRpcResult(self._role)


class _FakeUser:
    def __init__(self, uid="u-1", email="a@b.com"):
        self.id = uid
        self.email = email


@pytest.fixture
def patch_supabase(monkeypatch):
    """Swap app.auth.supabase for a fake returning the given user + role."""
    def _apply(user, role):
        monkeypatch.setattr(auth, "supabase", _FakeSupabase(user, role))
    return _apply


# ─── get_current_user ─────────────────────────────────────────────────────────

async def test_get_current_user_no_header():
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(None)
    assert exc.value.status_code == 401


async def test_get_current_user_missing_bearer_prefix():
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user("Token abc123")  # not "Bearer ..."
    assert exc.value.status_code == 401


async def test_get_current_user_invalid_token(patch_supabase):
    patch_supabase(user=None, role=None)  # supabase returns no user
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user("Bearer bad-token")
    assert exc.value.status_code == 401


async def test_get_current_user_valid(patch_supabase):
    patch_supabase(user=_FakeUser("u-9", "x@y.com"), role="manager")
    result = await auth.get_current_user("Bearer good-token")
    assert result == {"user_id": "u-9", "email": "x@y.com", "role": "manager"}


# ─── require_role ─────────────────────────────────────────────────────────────

async def test_require_role_insufficient_is_403(patch_supabase):
    patch_supabase(user=_FakeUser(), role="user")
    dep = auth.require_role("manager")
    with pytest.raises(HTTPException) as exc:
        await dep("Bearer good-token")
    assert exc.value.status_code == 403


async def test_require_role_sufficient_passes(patch_supabase):
    patch_supabase(user=_FakeUser("u-2"), role="admin")
    dep = auth.require_role("manager")
    result = await dep("Bearer good-token")
    assert result["role"] == "admin"


async def test_require_role_unauthenticated_is_401(patch_supabase):
    # No token at all -> 401 (from get_current_user), not 403.
    dep = auth.require_role("manager")
    with pytest.raises(HTTPException) as exc:
        await dep(None)
    assert exc.value.status_code == 401
