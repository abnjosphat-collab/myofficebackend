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
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timezone
import logging

from app.supabase_client import supabase
from app.auth import get_current_user, require_role, ROLE_ORDER

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

TABLE = "user_profiles"


class UserProfileUpdate(BaseModel):
    role: str


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
