"""PPE order list — the shared list of due or expiring PPE items someone has flagged to order.

It used to live in one browser's localStorage, so a colleague on another computer never saw it and clearing the browser
lost it. Each line is stored as the page builds it (`entry`, JSON) under the PPE record it is for (`record_id`), so adding
the same record twice never doubles a quantity: adding is idempotent.

Table: `ppe_order_list` (see supabase_migration_shared_lists.sql).
"""
import logging
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user
from app.db_helpers import fetch_all_pages
from app.supabase_client import supabase

logger = logging.getLogger(__name__)
router = APIRouter()

TABLE = "ppe_order_list"
MAX_LINES_PER_REQUEST = 500


class OrderLine(BaseModel):
    record_id: str = Field(..., min_length=1)
    employee_id: str = ""
    employee_name: str = ""
    ppe_type: str = ""
    item_name: str = ""
    size: str = ""
    expiry_date: str | None = None
    added_at: str | None = None


class AddLines(BaseModel):
    entries: List[OrderLine] = Field(..., max_length=MAX_LINES_PER_REQUEST)


class RemoveLines(BaseModel):
    record_ids: List[str] = Field(..., max_length=MAX_LINES_PER_REQUEST)


def _entry_of(row: dict[str, Any]) -> dict[str, Any]:
    """The line as the page wants it: the stored entry, with the time it was added."""
    entry = dict(row.get("entry") or {})
    entry["record_id"] = row.get("record_id", entry.get("record_id"))
    entry["added_at"] = entry.get("added_at") or row.get("added_at")
    return entry


@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def list_order_lines():
    try:
        data = await run_in_threadpool(lambda: fetch_all_pages(
            lambda start, end: supabase.table(TABLE).select("*").order("added_at", desc=False).order("record_id").range(start, end).execute()
        ))
    except Exception as e:
        logger.error(f"list_order_lines error: {e}")
        raise HTTPException(500, str(e))
    return [_entry_of(row) for row in data]


@router.post("")
@router.post("/")
async def add_order_lines(body: AddLines, current_user: dict = Depends(get_current_user)):
    """Add lines; a line whose record is already on the list is left as it is (never doubled)."""
    if not body.entries:
        return {"added": 0}
    who = current_user.get("email") or current_user.get("user_id") or ""
    payload = [{"record_id": e.record_id, "entry": e.model_dump(exclude_none=True), "added_by": who} for e in body.entries]
    try:
        result = await run_in_threadpool(lambda: supabase.table(TABLE).upsert(payload, on_conflict="record_id", ignore_duplicates=True).execute())
    except Exception as e:
        logger.error(f"add_order_lines error: {e}")
        raise HTTPException(500, str(e))
    return {"added": len(result.data or [])}


@router.post("/remove")
async def remove_order_lines(body: RemoveLines, current_user: dict = Depends(get_current_user)):
    """Remove the lines for these records (a line that is not there is not an error: the list already says what was asked)."""
    if not body.record_ids:
        return {"ok": True}
    try:
        await run_in_threadpool(lambda: supabase.table(TABLE).delete().in_("record_id", body.record_ids).execute())
    except Exception as e:
        logger.error(f"remove_order_lines error: {e}")
        raise HTTPException(500, str(e))
    return {"ok": True}


@router.delete("")
@router.delete("/")
async def clear_order_list(current_user: dict = Depends(get_current_user)):
    """Empty the whole list (after the order has been placed)."""
    try:
        await run_in_threadpool(lambda: supabase.table(TABLE).delete().neq("record_id", "").execute())
    except Exception as e:
        logger.error(f"clear_order_list error: {e}")
        raise HTTPException(500, str(e))
    return {"ok": True}
