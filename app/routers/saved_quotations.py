"""Saved quotations — the quotations people choose to keep, shared by everyone who signs in.

The quotation generator used to keep these in one browser. Each quotation is stored whole (`draft`, JSON) under its
quotation number, so saving the same number again updates it. The generator builds the PDF or Word file itself in the
browser; the server only keeps the drafts.

Table: `saved_quotations` (see supabase_migration_shared_lists.sql).
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user, role_at_least
from app.db_helpers import fetch_all_pages
from app.supabase_client import supabase, one_row

logger = logging.getLogger(__name__)
router = APIRouter()

TABLE = "saved_quotations"
MAX_DRAFT_CHARS = 400_000  # a quotation is a few KB; this leaves room for a long terms text, not for pasted files


class SaveQuotation(BaseModel):
    draft: Dict[str, Any]
    saved_at: str | None = None


def _out(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "savedAt": row.get("saved_at"), "savedBy": row.get("saved_by") or "", "draft": row.get("draft") or {}}


@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def list_quotations():
    try:
        data = await run_in_threadpool(lambda: fetch_all_pages(
            lambda start, end: supabase.table(TABLE).select("*").order("saved_at", desc=True).order("id").range(start, end).execute()
        ))
    except Exception as e:
        logger.error(f"list_quotations error: {e}")
        raise HTTPException(500, str(e))
    return [_out(row) for row in data]


@router.put("/{quotation_id}")
async def save_quotation(quotation_id: str, body: SaveQuotation, current_user: dict = Depends(get_current_user)):
    """Create the quotation, or update it when this number is already saved."""
    quotation_id = quotation_id.strip()
    if not quotation_id:
        raise HTTPException(422, "A quotation needs a number to be saved.")
    if len(str(body.draft)) > MAX_DRAFT_CHARS:
        raise HTTPException(422, "This quotation is too large to save.")
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": quotation_id,
        "draft": body.draft,
        "saved_at": body.saved_at or now,
        "saved_by": current_user.get("email") or "",
        "saved_by_id": current_user.get("user_id") or "",
        "updated_at": now,
    }
    try:
        result = await run_in_threadpool(lambda: supabase.table(TABLE).upsert(row, on_conflict="id").execute())
    except Exception as e:
        logger.error(f"save_quotation error: {e}")
        raise HTTPException(500, str(e))
    saved = one_row(result)
    if not saved:
        raise HTTPException(500, "The quotation was not saved.")
    return _out(saved)


@router.delete("/{quotation_id}")
async def delete_quotation(quotation_id: str, current_user: dict = Depends(get_current_user)):
    """The person who saved a quotation, or a manager or above, may delete it."""
    try:
        found = one_row(await run_in_threadpool(lambda: supabase.table(TABLE).select("id,saved_by_id").eq("id", quotation_id).execute()))
        if not found:
            return {"ok": True}  # already gone
        mine = bool(found.get("saved_by_id")) and found.get("saved_by_id") == current_user.get("user_id")
        if not mine and not role_at_least(current_user.get("role", "viewer"), "manager"):
            raise HTTPException(403, "Only the person who saved this quotation, or a manager, can delete it.")
        await run_in_threadpool(lambda: supabase.table(TABLE).delete().eq("id", quotation_id).execute())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"delete_quotation error: {e}")
        raise HTTPException(500, str(e))
    return {"ok": True}
