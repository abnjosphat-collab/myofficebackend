# backend/app/routers/admin.py — user list + role/permission management.
#
# This used to be handled entirely by the frontend calling Supabase directly
# (app/admin/page.tsx: `supabase.from('user_profiles').select/update(...)`),
# bypassing the backend — and therefore bypassing every auth check this
# session built. Moving it here means it goes through the same
# get_current_user/require_role gate as everything else, and this is now the
# one place that reads/writes user_profiles instead of the frontend doing it
# directly (matching lib/supabase.ts's own "all DB ops go through FastAPI"
# comment, which admin/page.tsx was the one exception to).
import os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timezone
import logging

from app.supabase_client import supabase
from app.auth import get_current_user, require_role, ROLE_ORDER

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

TABLE = "user_profiles"

# Used to build the redirect link in invite/password-reset emails.
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://myofficefrontend.vercel.app")

# Effectively-permanent ban duration for "deactivate" — Supabase's admin API
# has no literal "forever" value, only a duration string. ~100 years.
BAN_FOREVER = "876000h"


class UserProfileUpdate(BaseModel):
    role: str


class InviteUserRequest(BaseModel):
    email: str
    role: str = "user"


class SetActiveRequest(BaseModel):
    active: bool


def _load_target_for_self_admin_check(user_id: str) -> dict:
    """Fetch id/role/email and raise 404 if missing — shared by every
    endpoint below that needs the same self/super_admin protection lookup
    update_user already does."""
    existing = supabase.table(TABLE).select("id,role,email").eq("id", user_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return existing.data[0]


def _assert_may_act_on(caller_role: str, caller_id: str, target: dict, action: str):
    """Same rule as update_user's role-change guard, generalized to any
    admin action on another user: a plain admin can act on anyone except a
    super_admin or themselves; only a super_admin can do those."""
    is_self = target["id"] == caller_id
    if caller_role != "super_admin" and (target["role"] == "super_admin" or is_self):
        raise HTTPException(status_code=403, detail=f"Only a super_admin can {action} a super_admin or themselves.")


@router.get("/users")
async def list_users(current_user: dict = Depends(require_role("admin"))):
    """List every user profile — admin+ only."""
    try:
        result = supabase.table(TABLE).select("*").order("created_at", desc=False).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error listing users: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing users: {e}")


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    updated: UserProfileUpdate,
    current_user: dict = Depends(require_role("admin")),
):
    """
    Update a user's role/permissions. Mirrors the frontend's original
    `canEdit` rule (app/admin/page.tsx): a plain admin can edit anyone
    EXCEPT a super_admin or themselves — only a super_admin can do those,
    and only a super_admin can promote someone TO super_admin.
    """
    caller_role = current_user["role"]

    try:
        existing = supabase.table(TABLE).select("id,role").eq("id", user_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        target_role = existing.data[0]["role"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error looking up user: {e}")

    is_self = user_id == current_user["user_id"]
    if caller_role != "super_admin":
        if target_role == "super_admin" or is_self:
            raise HTTPException(status_code=403, detail="Only a super_admin can edit a super_admin or their own role.")
        if updated.role == "super_admin":
            raise HTTPException(status_code=403, detail="Only a super_admin can grant super_admin.")

    if updated.role not in ROLE_ORDER:
        raise HTTPException(status_code=400, detail=f"Invalid role '{updated.role}'.")

    try:
        result = supabase.table(TABLE).update({
            "role": updated.role,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", user_id).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="No data returned after update")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating user: {e}")


@router.post("/users/invite")
async def invite_user(body: InviteUserRequest, current_user: dict = Depends(require_role("admin"))):
    """Create a new Supabase Auth user and email them an invite link. The
    on_auth_user_created trigger creates their user_profiles row (role='user'
    by default) — this only needs to bump the role afterward if a different
    starting role was requested."""
    caller_role = current_user["role"]
    if body.role not in ROLE_ORDER:
        raise HTTPException(status_code=400, detail=f"Invalid role '{body.role}'.")
    if body.role in ("admin", "super_admin") and caller_role != "super_admin":
        raise HTTPException(status_code=403, detail="Only a super_admin can invite someone directly as admin/super_admin.")

    try:
        result = supabase.auth.admin.invite_user_by_email(
            body.email, {"redirect_to": f"{FRONTEND_URL}/auth/callback"}
        )
    except Exception as e:
        logger.error(f"Error inviting {body.email}: {e}")
        raise HTTPException(status_code=500, detail=f"Error sending invite: {e}")

    if not result.user:
        raise HTTPException(status_code=500, detail="Invite sent but no user record returned")

    if body.role != "user":
        try:
            supabase.table(TABLE).update({"role": body.role}).eq("id", result.user.id).execute()
        except Exception as e:
            # The invite already went out and the auth user exists — surface this as a
            # distinct warning rather than a full failure, since retrying the whole
            # invite would send a second email for what's really just a role bump.
            logger.error(f"Invited {body.email} but failed to set role={body.role}: {e}")
            raise HTTPException(status_code=207, detail=f"Invited, but failed to set role: {e}")

    return {"id": result.user.id, "email": body.email, "role": body.role}


@router.patch("/users/{user_id}/active")
async def set_user_active(
    user_id: str,
    body: SetActiveRequest,
    current_user: dict = Depends(require_role("admin")),
):
    """Deactivate (ban sign-in) or reactivate a user. Reversible — deliberately
    not a delete, so nothing elsewhere that references this user_id (approvals,
    created_by fields, etc.) is orphaned."""
    target = _load_target_for_self_admin_check(user_id)
    _assert_may_act_on(current_user["role"], current_user["user_id"], target, "deactivate")

    try:
        supabase.auth.admin.update_user_by_id(
            user_id, {"ban_duration": "none" if body.active else BAN_FOREVER}
        )
    except Exception as e:
        logger.error(f"Error setting ban state for {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating sign-in access: {e}")

    try:
        result = supabase.table(TABLE).update({
            "is_active": body.active,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", user_id).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="No data returned after update")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating is_active for {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating user: {e}")


@router.post("/users/{user_id}/reset-password")
async def send_password_reset(user_id: str, current_user: dict = Depends(require_role("admin"))):
    """Email the user a password-reset link. Uses the standard (non-admin)
    reset_password_for_email call — no elevated privilege needed for this
    specific action, it's the same thing a "forgot password" self-service
    flow would trigger."""
    target = _load_target_for_self_admin_check(user_id)
    _assert_may_act_on(current_user["role"], current_user["user_id"], target, "reset the password of")

    try:
        supabase.auth.reset_password_for_email(
            target["email"], {"redirect_to": f"{FRONTEND_URL}/auth/callback"}
        )
    except Exception as e:
        logger.error(f"Error sending password reset to {target['email']}: {e}")
        raise HTTPException(status_code=500, detail=f"Error sending password reset: {e}")

    return {"ok": True}
