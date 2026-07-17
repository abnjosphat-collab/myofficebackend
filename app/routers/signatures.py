# app/routers/signatures.py
# Stored signatures: save one reusable signature per user, and hand it back
# only after the caller re-enters their account password.
#
# The password check is the security boundary here. Once a signature image is
# replayable, drawing it proves nothing about identity — possession of the
# password is what attributes the approval to a person. So the check runs
# server-side, on the unlock request itself; a frontend-only check could be
# skipped by calling this API directly.

import os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
from supabase import create_client
from app.supabase_client import supabase
from app.auth import get_current_user
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Signatures"])

MAX_IMAGE_CHARS = 400_000  # ~300KB of base64; a pad PNG is ~15KB


class SignatureSave(BaseModel):
    image_data: str = Field(..., description="PNG data URL of the signature")
    source: str = Field(..., description="'drawn' or 'scanned'")


class UnlockRequest(BaseModel):
    password: str


def _verify_password(email: str, password: str) -> bool:
    """Check `password` against the user's Supabase Auth account.

    Uses a throwaway anon client rather than the shared `supabase` singleton:
    that one holds the service-role key, and sign_in_with_password() would
    store the returned user session on it, silently downgrading every
    subsequent request's privileges to that user.
    """
    url = os.getenv("SUPABASE_URL") or ""
    anon = os.getenv("SUPABASE_ANON_KEY") or ""
    if not url or not anon:
        logger.error("SUPABASE_ANON_KEY not set — cannot verify signing password")
        raise HTTPException(
            status_code=503,
            detail="Signature unlock is unavailable (server auth not configured).",
        )
    try:
        throwaway = create_client(url, anon)
        result = throwaway.auth.sign_in_with_password({"email": email, "password": password})
        return bool(result and result.user)
    except Exception:
        # Wrong password raises — that's an expected outcome, not an error.
        return False


@router.get("/me")
async def get_my_signature(user: dict = Depends(get_current_user)):
    """Whether the caller has a saved signature. Never returns the image —
    that requires POST /unlock with the password."""
    try:
        res = (
            supabase.table("user_signatures")
            .select("source, updated_at")
            .eq("user_id", user["user_id"])
            .execute()
        )
        row = res.data[0] if res.data else None
        if not row:
            return {"has_signature": False}
        return {"has_signature": True, "source": row["source"], "updated_at": row["updated_at"]}
    except Exception as e:
        logger.error(f"get_my_signature failed for {user['user_id']}: {e}")
        raise HTTPException(status_code=500, detail="Could not load signature.")


@router.put("/me")
async def save_my_signature(payload: SignatureSave, user: dict = Depends(get_current_user)):
    """Create or replace the caller's saved signature."""
    if payload.source not in ("drawn", "scanned"):
        raise HTTPException(status_code=400, detail="source must be 'drawn' or 'scanned'.")
    if not payload.image_data.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="image_data must be an image data URL.")
    if len(payload.image_data) > MAX_IMAGE_CHARS:
        raise HTTPException(status_code=413, detail="Signature image is too large.")

    try:
        supabase.table("user_signatures").upsert(
            {
                "user_id": user["user_id"],
                "image_data": payload.image_data,
                "source": payload.source,
            },
            on_conflict="user_id",
        ).execute()
        return {"ok": True}
    except Exception as e:
        logger.error(f"save_my_signature failed for {user['user_id']}: {e}")
        raise HTTPException(status_code=500, detail="Could not save signature.")


@router.delete("/me")
async def delete_my_signature(user: dict = Depends(get_current_user)):
    try:
        supabase.table("user_signatures").delete().eq("user_id", user["user_id"]).execute()
        return {"ok": True}
    except Exception as e:
        logger.error(f"delete_my_signature failed for {user['user_id']}: {e}")
        raise HTTPException(status_code=500, detail="Could not delete signature.")


@router.post("/unlock")
async def unlock_my_signature(payload: UnlockRequest, user: dict = Depends(get_current_user)):
    """Return the caller's saved signature image, given their account password."""
    if not _verify_password(user["email"], payload.password):
        raise HTTPException(status_code=401, detail="Incorrect password.")

    try:
        res = (
            supabase.table("user_signatures")
            .select("image_data, source")
            .eq("user_id", user["user_id"])
            .execute()
        )
        row = res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"unlock_my_signature lookup failed for {user['user_id']}: {e}")
        raise HTTPException(status_code=500, detail="Could not load signature.")

    if not row:
        raise HTTPException(status_code=404, detail="No saved signature. Draw one and save it first.")
    return {"image_data": row["image_data"], "source": row["source"]}
