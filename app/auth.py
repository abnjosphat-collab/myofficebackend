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

from fastapi import HTTPException, Header
from typing import Optional
from app.supabase_client import supabase
import logging

logger = logging.getLogger(__name__)

# Mirrors the role ordering already defined on the frontend (lib/supabase.ts
# ROLE_ORDER) — keep these in sync if roles ever change.
ROLE_ORDER = ['viewer', 'user', 'manager', 'admin', 'super_admin']


def role_at_least(role: str, min_role: str) -> bool:
    """True if `role` is at or above `min_role` in ROLE_ORDER. Unknown roles
    (not in ROLE_ORDER) are treated as insufficient rather than raising, so a
    bad/legacy role value fails closed instead of crashing the request."""
    try:
        return ROLE_ORDER.index(role) >= ROLE_ORDER.index(min_role)
    except ValueError:
        return False


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """
    Verify the Bearer JWT and fetch the caller's role.
    Raises 401 if unauthenticated or the token is invalid/expired.
    Returns {'user_id': str, 'email': str, 'role': str}.
    """
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Sign in required.')

    token = authorization.split(' ', 1)[1]

    try:
        resp = supabase.auth.get_user(token)
        user = resp.user
        if not user:
            raise HTTPException(status_code=401, detail='Invalid or expired session. Please sign in again.')
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f'Token verification failed: {e}')
        raise HTTPException(status_code=401, detail='Invalid or expired session. Please sign in again.')

    # Fetch role via security-definer function (bypasses RLS so the anon client can call it).
    # A lookup that SUCCEEDS but returns nothing → the user simply has no elevated role yet,
    # so 'user' is the correct default. A lookup that FAILS (RPC error) is different: silently
    # falling back to 'user' would strip a manager's privileges on a transient blip and hide
    # the outage. Fail loud with a retryable 503 instead of a silent downgrade.
    try:
        role_resp = supabase.rpc('get_user_role_by_id', {'user_id': str(user.id)}).execute()
        role = role_resp.data if role_resp.data else 'user'
    except Exception as e:
        logger.error(f'Role lookup failed for {user.id}: {e}')
        raise HTTPException(
            status_code=503,
            detail='Could not verify your permissions right now. Please try again in a moment.',
        )

    return {'user_id': str(user.id), 'email': user.email or '', 'role': role}


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
