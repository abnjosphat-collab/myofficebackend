# tests/test_auth.py — the role-based access control in app/auth.py, the core of
# the whole authorization scheme (get_current_user + require_role, applied to 159+
# endpoints). Pure-logic + mocked-Supabase; no network, no live server.

import asyncio
import time

import pytest
from supabase_auth.errors import AuthRetryableError
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
    def __init__(self, user, role, scope=None):
        self.auth = _FakeAuth(user)
        self._role = role
        self._scope = scope

    def rpc(self, name, params):
        self._last_rpc = (name, params)
        return self

    def execute(self):
        if self._last_rpc[0] == "get_user_scope_by_id":
            return _FakeRpcResult(self._scope)
        return _FakeRpcResult(self._role)


class _FakeUser:
    def __init__(self, uid="u-1", email="a@b.com", factors=None):
        self.id = uid
        self.email = email
        # Real Supabase get_user populates `factors` (supabase_auth User model,
        # Optional[List[Factor]]); [] is the honest default for "not enrolled".
        self.factors = factors if factors is not None else []


@pytest.fixture
def patch_supabase(monkeypatch):
    """Swap app.auth.supabase for a fake returning the given user + role."""
    def _apply(user, role, scope=None):
        monkeypatch.setattr(auth, "supabase", _FakeSupabase(user, role, scope))
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


async def test_auth_service_outage_is_retryable_not_invalid_session(patch_supabase):
    patch_supabase(user=_FakeUser(), role="user")

    def unavailable(_token):
        raise AuthRetryableError("Service unavailable", 503)

    auth.supabase.auth.get_user = unavailable
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user("Bearer good-token")
    assert exc.value.status_code == 503


async def test_get_current_user_valid(patch_supabase):
    patch_supabase(user=_FakeUser("u-9", "x@y.com"), role="manager")
    result = await auth.get_current_user("Bearer good-token")
    assert result == {"user_id": "u-9", "email": "x@y.com", "role": "manager", "name": "X", "scope": None}


async def test_slow_auth_does_not_block_other_requests(patch_supabase):
    patch_supabase(user=_FakeUser(), role="user")
    original = auth.supabase.auth.get_user

    def slow_get_user(token):
        time.sleep(0.1)
        return original(token)

    auth.supabase.auth.get_user = slow_get_user
    pending = asyncio.create_task(auth.get_current_user("Bearer good-token"))
    await asyncio.sleep(0.01)
    assert not pending.done(), "The event loop should remain free during Auth I/O"
    assert (await pending)["role"] == "user"


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


# ─── get_current_user_optional ─────────────────────────────────────────────────

async def test_get_current_user_optional_no_header_returns_none():
    assert await auth.get_current_user_optional(None) is None


async def test_get_current_user_optional_missing_bearer_prefix_returns_none():
    assert await auth.get_current_user_optional("Token abc123") is None


async def test_get_current_user_optional_invalid_token_returns_none(patch_supabase):
    # An expired/bad token must degrade to "anonymous", never raise — this is what
    # lets unsigned-in visitors' usage events still be accepted.
    patch_supabase(user=None, role=None)
    assert await auth.get_current_user_optional("Bearer bad-token") is None


async def test_get_current_user_optional_valid_token_returns_user(patch_supabase):
    patch_supabase(user=_FakeUser("u-9", "x@y.com"), role="manager")
    result = await auth.get_current_user_optional("Bearer good-token")
    assert result == {"user_id": "u-9", "email": "x@y.com", "role": "manager", "name": "X", "scope": None}


# ─── MFA gate (server-side aal2 enforcement) ──────────────────────────────────

def _jwt_with_aal(aal):
    """An unsigned access-token-shaped string carrying the given aal claim.
    get_user is faked in these tests, so the signature is irrelevant — the
    gate only base64-decodes the payload after validation succeeds."""
    import base64
    import json

    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    payload = {} if aal is None else {"aal": aal}
    return f"{b64({'alg': 'none'})}.{b64(payload)}.sig"


async def test_mfa_enrolled_aal1_is_401_mfa_required(patch_supabase):
    patch_supabase(
        user=_FakeUser("u-9", "x@y.com", factors=[{"status": "verified"}]),
        role="manager",
    )
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(f"Bearer {_jwt_with_aal('aal1')}")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Two-factor verification required."
    assert exc.value.headers == {"X-Auth-Reason": "mfa_required"}


async def test_mfa_enrolled_aal2_passes(patch_supabase):
    patch_supabase(
        user=_FakeUser("u-9", "x@y.com", factors=[{"status": "verified"}]),
        role="manager",
    )
    result = await auth.get_current_user(f"Bearer {_jwt_with_aal('aal2')}")
    assert result == {"user_id": "u-9", "email": "x@y.com", "role": "manager", "name": "X", "scope": None}


async def test_mfa_unenrolled_aal1_passes(patch_supabase):
    # No verified factor (and no unverified one either) → nothing to
    # challenge, so aal is irrelevant.
    patch_supabase(user=_FakeUser("u-9", "x@y.com", factors=[]), role="user")
    result = await auth.get_current_user(f"Bearer {_jwt_with_aal('aal1')}")
    assert result["role"] == "user"


async def test_mfa_unverified_factor_only_passes(patch_supabase):
    # A factor that was enrolled but never confirmed is not a verified
    # factor — it must not gate the user.
    patch_supabase(
        user=_FakeUser("u-9", "x@y.com", factors=[{"status": "unverified"}]),
        role="user",
    )
    result = await auth.get_current_user(f"Bearer {_jwt_with_aal('aal1')}")
    assert result["role"] == "user"


async def test_mfa_flag_off_passes_enrolled_aal1(patch_supabase, monkeypatch):
    # REQUIRE_MFA_AAL2=false is the emergency off-switch.
    monkeypatch.setattr(auth, "REQUIRE_MFA_AAL2", False)
    patch_supabase(
        user=_FakeUser("u-9", "x@y.com", factors=[{"status": "verified"}]),
        role="manager",
    )
    result = await auth.get_current_user(f"Bearer {_jwt_with_aal('aal1')}")
    assert result["role"] == "manager"


async def test_mfa_malformed_token_claim_reads_as_aal1(patch_supabase):
    # "valid-token" is not a JWT at all — the claim decode fails closed to
    # aal1, so an enrolled user is gated, ...
    assert auth.aal_from_token("valid-token") == "aal1"
    assert auth.aal_from_token("a.b") == "aal1"
    assert auth.aal_from_token(_jwt_with_aal(None)) == "aal1"
    assert auth.aal_from_token(_jwt_with_aal("aal3")) == "aal1"
    patch_supabase(
        user=_FakeUser("u-9", "x@y.com", factors=[{"status": "verified"}]),
        role="manager",
    )
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user("Bearer [REDACTED]")
    assert exc.value.status_code == 401
    assert exc.value.headers == {"X-Auth-Reason": "mfa_required"}


async def test_mfa_malformed_claim_unenrolled_passes(patch_supabase):
    # ... while an unenrolled user with the same non-JWT token is unaffected.
    patch_supabase(user=_FakeUser("u-9", "x@y.com", factors=[]), role="user")
    result = await auth.get_current_user("Bearer [REDACTED]")
    assert result["role"] == "user"


async def test_mfa_factor_objects_with_status_attr(patch_supabase):
    # Real SDK factors are Factor models (attribute access), not dicts.
    class _Factor:
        status = "verified"

    patch_supabase(user=_FakeUser("u-9", "x@y.com", factors=[_Factor()]), role="user")
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(f"Bearer {_jwt_with_aal('aal1')}")
    assert exc.value.headers == {"X-Auth-Reason": "mfa_required"}


async def test_mfa_unpopulated_factors_falls_back_to_admin_list(patch_supabase, monkeypatch):
    # factors=None (unpopulated) → one admin factor-list call, whose answer
    # is cached: the second request reuses it without a second call.
    calls = []

    def fake_admin_list(user_id):
        calls.append(user_id)
        return True

    monkeypatch.setattr(auth, "_admin_has_verified_factor", fake_admin_list)
    user = _FakeUser("u-9", "x@y.com")
    user.factors = None
    patch_supabase(user=user, role="user")
    assert await auth.user_has_verified_factor("u-9", user) is True
    assert await auth.user_has_verified_factor("u-9", user) is True
    assert calls == ["u-9"]


async def test_mfa_admin_list_failure_is_retryable_503(patch_supabase, monkeypatch):
    def boom(user_id):
        raise RuntimeError("auth admin down")

    monkeypatch.setattr(auth, "_admin_has_verified_factor", boom)
    user = _FakeUser("u-9", "x@y.com")
    user.factors = None
    patch_supabase(user=user, role="user")
    with pytest.raises(HTTPException) as exc:
        await auth.user_has_verified_factor("u-9", user)
    assert exc.value.status_code == 503


# ─── Auth cache ───────────────────────────────────────────────────────────────

async def test_auth_cache_second_call_skips_supabase(patch_supabase, monkeypatch):
    patch_supabase(user=_FakeUser("u-9", "x@y.com", factors=[]), role="manager")
    get_user_calls = []
    rpc_calls = []
    orig_get_user = auth.supabase.auth.get_user
    orig_rpc = auth.supabase.rpc

    def counting_get_user(token):
        get_user_calls.append(token)
        return orig_get_user(token)

    def counting_rpc(name, params):
        rpc_calls.append((name, params))
        return orig_rpc(name, params)

    auth.supabase.auth.get_user = counting_get_user
    auth.supabase.rpc = counting_rpc
    first = await auth.get_current_user("Bearer [REDACTED]")
    second = await auth.get_current_user("Bearer [REDACTED]")
    assert first == second == {"user_id": "u-9", "email": "x@y.com", "role": "manager", "name": "X", "scope": None}
    assert len(get_user_calls) == 1, "2nd request must not re-verify the token"
    assert len(rpc_calls) == 2, "role + scope RPCs run once on the 1st request, never on the 2nd"


async def test_auth_cache_ttl_expiry_revalidates(patch_supabase, monkeypatch):
    import time as time_mod

    patch_supabase(user=_FakeUser("u-9", "x@y.com", factors=[]), role="manager")
    monkeypatch.setenv("AUTH_CACHE_TTL_SECONDS", "30")
    now = [1000.0]
    monkeypatch.setattr(time_mod, "monotonic", lambda: now[0])
    calls = []
    orig = auth.supabase.auth.get_user

    def counting(token):
        calls.append(token)
        return orig(token)

    auth.supabase.auth.get_user = counting
    await auth.get_current_user("Bearer [REDACTED]")
    now[0] += 31  # past the 30s TTL
    await auth.get_current_user("Bearer [REDACTED]")
    assert len(calls) == 2, "expired entry must re-verify"


async def test_auth_cache_is_bounded(patch_supabase):
    patch_supabase(user=_FakeUser("u-9", "x@y.com", factors=[]), role="user")
    for i in range(auth._AUTH_CACHE_MAX + 50):
        await auth.get_current_user("Bearer " + f"tok-{i}")
    assert len(auth._auth_cache) <= auth._AUTH_CACHE_MAX


async def test_auth_cache_never_caches_failures(patch_supabase):
    # A 401 (no user) followed by the same token must re-verify, not replay
    # the cached failure — e.g. a token that becomes valid must work.
    patch_supabase(user=None, role=None)
    with pytest.raises(HTTPException):
        await auth.get_current_user("Bearer [REDACTED]")
    assert auth._auth_cache == {}
    patch_supabase(user=_FakeUser("u-9", "x@y.com", factors=[]), role="user")
    result = await auth.get_current_user("Bearer [REDACTED]")
    assert result["role"] == "user"


async def test_auth_cache_503_is_not_cached(patch_supabase):
    patch_supabase(user=_FakeUser(), role="user")

    def unavailable(_token):
        raise AuthRetryableError("Service unavailable", 503)

    auth.supabase.auth.get_user = unavailable
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user("Bearer [REDACTED]")
    assert exc.value.status_code == 503
    assert auth._auth_cache == {}


async def test_invalidate_user_drops_entries_and_factor_answer(patch_supabase):
    patch_supabase(user=_FakeUser("u-9", "x@y.com", factors=[]), role="manager")
    await auth.get_current_user("Bearer [REDACTED]")
    auth._factor_cache["u-9"] = (9999999999.0, True)
    assert auth._auth_cache, "precondition: something cached"
    auth.invalidate_user("u-9")
    assert auth._auth_cache == {}
    assert "u-9" not in auth._factor_cache


async def test_invalidate_user_keeps_other_users(patch_supabase):
    patch_supabase(user=_FakeUser("u-9", "x@y.com", factors=[]), role="manager")
    await auth.get_current_user("Bearer " + "tok-A")
    other = _FakeUser("u-10", "other@x.com", factors=[])
    auth.supabase.auth = _FakeAuth(other)
    await auth.get_current_user("Bearer " + "tok-B")
    auth.invalidate_user("u-9")
    assert len(auth._auth_cache) == 1
    assert list(auth._auth_cache.values())[0][1]["user_id"] == "u-10"


async def test_cached_entry_from_flag_off_is_reverified_when_flag_on(patch_supabase, monkeypatch):
    # Cached while the gate was off (mfa_ok False) must not be honoured once
    # the gate is back on — the enrolled aal1 caller is re-gated.
    monkeypatch.setattr(auth, "REQUIRE_MFA_AAL2", False)
    patch_supabase(
        user=_FakeUser("u-9", "x@y.com", factors=[{"status": "verified"}]),
        role="manager",
    )
    token = f"Bearer {_jwt_with_aal('aal1')}"
    assert (await auth.get_current_user(token))["role"] == "manager"
    monkeypatch.setattr(auth, "REQUIRE_MFA_AAL2", True)
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(token)
    assert exc.value.headers == {"X-Auth-Reason": "mfa_required"}


# ─── display_name: who a record says approved or changed it ─────────────────────────────────────────────────────────

class _U:
    def __init__(self, email="", meta=None):
        self.email = email
        self.user_metadata = meta


def test_display_name_prefers_the_profile_name():
    assert auth.display_name(_U("x@y.z", {"full_name": "  Elias   Mwazha "})) == "Elias Mwazha"
    assert auth.display_name(_U("x@y.z", {"name": "Edson Mavhondo"})) == "Edson Mavhondo"


def test_display_name_falls_back_to_a_readable_email():
    assert auth.display_name(_U("tafadzwa.matangira@dallaglio.co.zw", {})) == "Tafadzwa Matangira"
    assert auth.display_name(_U("o_neil-smith@x.com", None)) == "O Neil Smith"
    assert auth.display_name(_U("", None)) == ""
