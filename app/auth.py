# backend/app/auth.py
# JWT verification for approval/rejection endpoints.
# The Supabase Python client validates the user's access token against
# the Supabase auth service. Role is fetched via a security-definer
# RPC function so RLS doesn't block the anon-key client.

from fastapi import HTTPException
from typing import Optional
from app.supabase_client import supabase
import logging

logger = logging.getLogger(__name__)

APPROVAL_ROLES = {'manager', 'admin', 'super_admin'}


async def verify_approver(authorization: Optional[str]) -> dict:
    """
    Verify the Bearer JWT and confirm the user has manager+ role.
    Raises 401 if unauthenticated, 403 if insufficient role.
    Returns {'user_id': str, 'email': str, 'role': str}.
    """
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(
            status_code=401,
            detail='Sign in required to approve or reject requests.'
        )

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

    # Fetch role via security-definer function (bypasses RLS so anon client can call it)
    try:
        role_resp = supabase.rpc('get_user_role_by_id', {'user_id': str(user.id)}).execute()
        role = role_resp.data if role_resp.data else 'user'
    except Exception as e:
        logger.warning(f'Role lookup failed for {user.id}: {e}')
        role = 'user'

    if role not in APPROVAL_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f'Permission denied. Approvals require Manager role or above. Your role: {role}.'
        )

    return {'user_id': str(user.id), 'email': user.email or '', 'role': role}
