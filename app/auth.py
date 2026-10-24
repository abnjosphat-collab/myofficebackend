# backend/app/auth.py
# JWT verification and role-based access control, backed by Supabase Auth.
# The Supabase Python client validates the caller's access token against the
# Supabase auth service. Role is fetched via a security-definer RPC function
# so RLS doesn't block the anon-key client.
#
# Two ways to use this:
#   1. Depends(require_role('manager')) on a route signature — the whole
#      endpoint requires manager+ role, enforced before the handler runs.
#   2. `await require_role('manager')(authorization)` called directly inside
#      a handler — for endpoints that only require a role CONDITIONALLY
#      (e.g. leaves.py/overtime.py only check on approve/reject, not on
#      every field edit), where a route-level Depends would over-apply auth
#      to actions that don't need it.
#
# MFA gate: the frontend already holds enrolled users at a 2FA challenge
# (lib/auth-context.tsx), but nothing on the server checked — an aal1 token
# for an enrolled user worked everywhere. get_current_user now enforces it:
# a verified TOTP factor + anything but aal2 → 401 mfa_required.
# REQUIRE_MFA_AAL2=false is the emergency off-switch.
#
# Auth cache: every authenticated request used to cost 3 Supabase round-trips
# (get_user + role RPC + scope RPC). Verified results are cached in-process for
# AUTH_CACHE_TTL_SECONDS (default 30s), keyed by sha256(token) — tokens and
# hashes never leave the process (never Redis). Trade-off: a role/scope change
# takes up to TTL to be seen elsewhere; admin role/scope/deactivate/MFA-reset
# endpoints call invalidate_user() so those take effect immediately.

from fastapi import HTTPException, Header
from starlette.concurrency import run_in_threadpool
from supabase_auth.errors import AuthRetryableError
import base64
import hashlib
import httpx
import json
import os
import time
from typing import Optional
from app.supabase_client import supabase, SUPABASE_URL, SUPABASE_KEY
from app.audit import set_actor
import logging

logger = logging.getLogger(__name__)

# Mirrors the role ordering already defined on the frontend (lib/supabase.ts
# ROLE_ORDER) — keep these in sync if roles ever change.
ROLE_ORDER = ['viewer', 'user', 'manager', 'admin', 'super_admin']

# Emergency off-switch for the server-side MFA gate. Default true.
REQUIRE_MFA_AAL2 = os.environ.get("REQUIRE_MFA_AAL2", "true").strip().lower() not in ("0", "false", "no", "off")


def _cache_ttl() -> int:
    try:
        return max(0, int(os.environ.get("AUTH_CACHE_TTL_SECONDS", "30")))
    except ValueError:
        return 30


# NOTE: the TTL env var (AUTH_CACHE_TTL_SECONDS, default 30) is read fresh on
# every cache decision via _cache_ttl(), not frozen at import — there is
# deliberately no module-level constant to go stale.
_AUTH_CACHE_MAX = 1000

# sha256(token) -> (expires_at_monotonic, user_dict, mfa_ok). mfa_ok records
# whether the MFA gate passed (or was not applicable) when the entry was
# stored; a hit with the flag on but mfa_ok False is treated as a miss and
# re-verified, so flipping REQUIRE_MFA_AAL2 back on never honours a stale
# unchecked entry.
_auth_cache: dict[str, tuple[float, dict, bool]] = {}
# user_id -> (expires_at_monotonic, has_verified_factor). The admin
# factor-list fallback answer, so an enrolled user's every request doesn't
# cost a third Supabase round-trip.
_factor_cache: dict[str, tuple[float, bool]] = {}


def _reset_for_tests() -> None:
    """Clear auth + factor caches between tests (called from conftest)."""
    _auth_cache.clear()
    _factor_cache.clear()


def invalidate_user(user_id: str) -> None:
    """Drop every cached entry for `user_id` (auth results + factor answer).

    Called from the admin role/scope-change, deactivate and MFA-factor-reset
    endpoints so those take effect immediately instead of at TTL expiry.
    """
    for key, (_exp, cached, _mfa_ok) in list(_auth_cache.items()):
        if cached.get("user_id") == user_id:
            _auth_cache.pop(key, None)
    _factor_cache.pop(user_id, None)


def _store_auth_cache(key: str, user: dict, mfa_ok: bool) -> None:
    ttl = _cache_ttl()
    if ttl <= 0:
        return
    now = time.monotonic()
    if len(_auth_cache) >= _AUTH_CACHE_MAX and key not in _auth_cache:
        for candidate, (expires_at, _u, _m) in list(_auth_cache.items()):
            if expires_at <= now:
                _auth_cache.pop(candidate, None)
        if len(_auth_cache) >= _AUTH_CACHE_MAX:
            oldest = min(_auth_cache, key=lambda k: _auth_cache[k][0])
            _auth_cache.pop(oldest, None)
    _auth_cache[key] = (now + ttl, dict(user), mfa_ok)


def _lookup_auth_cache(key: str) -> Optional[tuple[dict, bool]]:
    entry = _auth_cache.get(key)
    if entry is None:
        return None
    expires_at, user, mfa_ok = entry
    if expires_at <= time.monotonic():
        _auth_cache.pop(key, None)
        return None
    return dict(user), mfa_ok


def role_at_least(role: str, min_role: str) -> bool:
    """True if `role` is at or above `min_role` in ROLE_ORDER. Unknown roles
    (not in ROLE_ORDER) are treated as insufficient rather than raising, so a
    bad/legacy role value fails closed instead of crashing the request."""
    try:
        return ROLE_ORDER.index(role) >= ROLE_ORDER.index(min_role)
    except ValueError:
        return False


def aal_from_token(token: str) -> str:
    """The `aal` claim of an access token, WITHOUT verifying the signature.

    Only ever called after supabase.auth.get_user(token) already validated
    the token — this just reads the claim off it. Anything malformed or
    missing reads as 'aal1' (fail closed for enrolled users: they must
    present a real aal2 token to pass the gate).
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return "aal1"
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        aal = payload.get("aal") if isinstance(payload, dict) else None
        return aal if aal in ("aal1", "aal2") else "aal1"
    except Exception:
        return "aal1"


def _verified_factor_in(user) -> Optional[bool]:
    """True/False from get_user's own `factors`, or None when unpopulated.

    supabase_auth's User model carries `factors` (Optional[List[Factor]];
    its own _list_factors reads `response.user.factors or []`), so the
    normal get_user response already answers this with no extra call.
    """
    factors = getattr(user, "factors", None)
    if factors is None:
        return None
    try:
        return any(
            (f.get("status") if isinstance(f, dict) else getattr(f, "status", None)) == "verified"
            for f in factors
        )
    except TypeError:
        return None


def _admin_has_verified_factor(user_id: str) -> bool:
    """Fallback: list the user's factors via the Auth Admin API (service key).

    Plain HTTPS like routers/admin.py's helper — supabase-py only exposes
    factor management as private methods, so this doesn't depend on SDK
    internals. Raises on transport failure (the caller turns that into a
    retryable 503, same as a failed role lookup).
    """
    import urllib.request
    import urllib.error

    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    req = urllib.request.Request(
        SUPABASE_URL.rstrip("/") + f"/auth/v1/admin/users/{user_id}/factors",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            listed = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"factor list failed (HTTP {e.code})")
    factor_lists = listed.values() if isinstance(listed, dict) else []
    for group in factor_lists:
        if isinstance(group, list):
            for f in group:
                if isinstance(f, dict) and f.get("status") == "verified":
                    return True
    return False


async def user_has_verified_factor(user_id: str, user) -> bool:
    """Whether the user has any verified MFA factor (cached per user_id).

    get_user's own `factors` answer first (no extra round-trip); only when
    unpopulated does this fall back to the admin factor-list call, whose
    answer is cached for AUTH_CACHE_TTL_SECONDS.
    """
    direct = _verified_factor_in(user)
    if direct is not None:
        return direct
    now = time.monotonic()
    cached = _factor_cache.get(user_id)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        has_factor = await run_in_threadpool(_admin_has_verified_factor, user_id)
    except Exception as e:
        logger.error(f"MFA factor lookup failed for {user_id}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Could not verify your sign-in security right now. Please try again in a moment.",
        )
    ttl = _cache_ttl()
    if ttl > 0:
        _factor_cache[user_id] = (now + ttl, has_factor)
    return has_factor


def display_name(user) -> str:
    """The person's name as it should appear on a record they approved or changed: the name on their profile, else
    their email's local part made readable ("elias.mwazha@x" becomes "Elias Mwazha"). Always from the verified
    session, never from anything the browser sends."""
    meta = getattr(user, 'user_metadata', None) or {}
    name = str(meta.get('full_name') or meta.get('name') or '').strip()
    if name:
        return ' '.join(name.split())
    local = (getattr(user, 'email', '') or '').split('@')[0]
    return ' '.join(part.capitalize() for part in local.replace('_', '.').replace('-', '.').split('.') if part)


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """
    Verify the Bearer [REDACTED] and fetch the caller's role and section scope.
    Raises 401 if unauthenticated or the token is invalid/expired.
    Returns {'user_id': str, 'email': str, 'role': str, 'name': str,
    'scope': Optional[str]} (scope None = not limited to a section).
    """
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Sign in required.')

    token = authorization.split(' ', 1)[1]

    cache_key = hashlib.sha256(token.encode()).hexdigest()
    hit = _lookup_auth_cache(cache_key)
    if hit is not None:
        cached_user, mfa_ok = hit
        if not REQUIRE_MFA_AAL2 or mfa_ok:
            set_actor(cached_user)
            return cached_user
        # The gate was off (or bypassed) when this was cached and is on now:
        # fall through and re-verify instead of honouring a stale entry.

    try:
        # supabase-py is synchronous. Keep its network call off the event loop so
        # concurrent authenticated reads cannot queue behind a slow Auth request.
        resp = await run_in_threadpool(supabase.auth.get_user, token)
        if resp is None:
            raise HTTPException(status_code=401, detail='Invalid or expired session. Please sign in again.')
        user = resp.user
        if not user:
            raise HTTPException(status_code=401, detail='Invalid or expired session. Please sign in again.')
    except HTTPException:
        raise
    except (AuthRetryableError, httpx.TransportError, httpx.TimeoutException) as e:
        logger.warning(f'Auth service temporarily unavailable: {e}')
        raise HTTPException(status_code=503, detail='Sign-in verification is temporarily unavailable. Please retry.')
    except Exception as e:
        logger.warning(f'Token verification failed: {e}')
        raise HTTPException(status_code=401, detail='Invalid or expired session. Please sign in again.')

    # Server-side MFA gate (the frontend challenge alone is not enforcement:
    # an aal1 token used to work everywhere). Only enrolled users are gated —
    # no verified factor means nothing to challenge, so aal is irrelevant.
    # mfa_ok stays False when the gate is off: an entry cached while
    # REQUIRE_MFA_AAL2=false must be re-verified once the flag is back on.
    mfa_ok = False
    if not REQUIRE_MFA_AAL2:
        pass
    elif await user_has_verified_factor(str(user.id), user):
        if aal_from_token(token) != "aal2":
            raise HTTPException(
                status_code=401,
                detail="Two-factor verification required.",
                headers={"X-Auth-Reason": "mfa_required"},
            )
        mfa_ok = True
    else:
        mfa_ok = True

    # Fetch role via security-definer function (bypasses RLS so the anon client can call it).
    # A lookup that SUCCEEDS but returns nothing → the user simply has no elevated role yet,
    # so 'user' is the correct default. A lookup that FAILS (RPC error) is different: silently
    # falling back to 'user' would strip a manager's privileges on a transient blip and hide
    # the outage. Fail loud with a retryable 503 instead of a silent downgrade.
    try:
        role_resp = await run_in_threadpool(
            lambda: supabase.rpc('get_user_role_by_id', {'user_id': str(user.id)}).execute()
        )
        role = role_resp.data if role_resp.data else 'user'
    except Exception as e:
        logger.error(f'Role lookup failed for {user.id}: {e}')
        raise HTTPException(
            status_code=503,
            detail='Could not verify your permissions right now. Please try again in a moment.',
        )

    # Section scope comes from the sibling RPC over
    # user_profiles.permissions->>'scope' (NULL/empty = not limited). Same
    # failure contract as the role lookup: an RPC error is a loud retryable
    # 503 — including a missing function before the Batch 1 migration is
    # applied — never a silent "unscoped", which would leak cross-section
    # data to a limited account.
    try:
        scope_resp = await run_in_threadpool(
            lambda: supabase.rpc('get_user_scope_by_id', {'user_id': str(user.id)}).execute()
        )
        scope = scope_resp.data.strip() if isinstance(scope_resp.data, str) and scope_resp.data.strip() else None
    except Exception as e:
        logger.error(f'Scope lookup failed for {user.id}: {e}')
        raise HTTPException(
            status_code=503,
            detail='Could not verify your permissions right now. Please try again in a moment.',
        )

    result = {'user_id': str(user.id), 'email': user.email or '', 'role': role, 'name': display_name(user), 'scope': scope}
    # Only verified successes are cached — 401s, 503s and the MFA 401 above
    # raise before reaching here, so failures are never served from cache.
    _store_auth_cache(cache_key, result, mfa_ok)
    set_actor(result)
    return result


async def get_current_user_optional(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """
    Same as get_current_user, but returns None instead of raising when there's no
    (or an invalid/expired) token. For endpoints that must accept anonymous callers
    - e.g. usage-event ingestion, which needs to record unsigned-in visitors too -
    but still want to attribute the event to a user when one is signed in.
    """
    if not authorization or not authorization.startswith('Bearer '):
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None


def require_role(min_role: str):
    """
    Returns an async dependency requiring the caller to be signed in with at
    least `min_role`. Raises 401 if unauthenticated, 403 if the role is
    insufficient.

    Usage:
      - Route-wide:  Depends(require_role('manager'))
      - Conditional: approver = await require_role('manager')(authorization)
    """
    async def _check(authorization: Optional[str] = Header(None)) -> dict:
        user = await get_current_user(authorization)
        if not role_at_least(user['role'], min_role):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied. Requires {min_role}+ role. Your role: {user['role']}."
            )
        return user
    return _check


async def require_role_if_status_in(
    status: Optional[str], trigger_statuses: set, min_role: str,
    authorization: Optional[str], context: str = "Status change",
) -> Optional[dict]:
    """The "conditional" half of the pattern this module's own docstring already
    named above: an edit endpoint that only needs an elevated role for SOME status
    values (approve/reject) and not others (any other field edit, which just needs
    to be signed in via the route's normal current_user dependency). Was hand-rolled
    identically in leaves.py and overtime.py's PATCH handlers; centralised here.

    No-op (returns None) if `status` isn't one of `trigger_statuses`. Otherwise
    raises the same 401/403 require_role(min_role) already would, and logs who did
    it — `context` customises that log line's wording per call site.
    """
    if status not in trigger_statuses:
        return None
    approver = await require_role(min_role)(authorization)
    logger.info(f"{context} '{status}' by {approver['email']} (role: {approver['role']})")
    return approver
