"""PPE order list — the shared list of due or expiring PPE items someone has flagged to order.

It used to live in one browser's localStorage, so a colleague on another computer never saw it and clearing the browser
lost it. Each line is stored as the page builds it (`entry`, JSON) under the PPE record it is for (`record_id`), so adding
the same record twice never doubles a quantity: adding is idempotent.

Table: `ppe_order_list` (see supabase_migration_shared_lists.sql).
"""
import logging
import re
from typing import Any, List, Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user
from app.db_helpers import fetch_all_pages
from app.supabase_client import supabase
from app.errors import server_error
from app.scope import assert_row_visible, filter_rows, person_ids_for, row_visible, viewer_scope

logger = logging.getLogger(__name__)
router = APIRouter()

TABLE = "ppe_order_list"
MAX_LINES_PER_REQUEST = 500


class OrderLine(BaseModel):
    """One line on the shared list. Old rows may lack the newer fields; reads pass them through untouched."""

    record_id: str = Field(..., min_length=1)
    employee_id: str = ""
    employee_name: str = ""
    ppe_type: str = ""
    item_name: str = ""
    size: str = ""
    expiry_date: str | None = None
    added_at: str | None = None
    kind: Literal["line", "free", "excluded"] = "line"
    source: Literal["manual"] = "manual"
    qty: int = Field(default=1, ge=1)
    note: str = ""


class AddLines(BaseModel):
    entries: List[OrderLine] = Field(..., max_length=MAX_LINES_PER_REQUEST)


# Order-list ids: a record id, an exclusion (x:<record id>:<expiry at the time>), or a free line (free:<uuid>).
RECORD_ID_RE = re.compile(r"^(x:\d+:.*|free:[0-9a-f-]+|\d+)$")


def _check_record_ids(entries: list[OrderLine]) -> None:
    bad = [e.record_id for e in entries if not RECORD_ID_RE.match(e.record_id)]
    if bad:
        raise HTTPException(status_code=422, detail=f"These are not order-list ids: {', '.join(bad)}.")


class PatchLine(BaseModel):
    qty: int | None = Field(default=None, ge=1)
    size: str | None = None
    note: str | None = None
    kind: Literal["line"] | None = None
    source: Literal["manual"] | None = None


@router.patch("/{record_id}")
async def update_order_line(record_id: str, body: PatchLine, current_user: dict = Depends(get_current_user)):
    """Change a line's quantity, size or note. Kind/source only graduate a legacy line (stored without kind);
    anything else is refused. Read-modify-write with no guard: simultaneous edits last-write-wins."""
    try:
        found = await run_in_threadpool(
            lambda: supabase.table(TABLE).select("*").eq("record_id", record_id).execute()
        )
    except Exception as e:
        logger.error(f"update_order_line read error: {e}")
        raise server_error(e, "Reading order line")
    rows = found.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="That order line is not on the list.")
    first = rows[0] if isinstance(rows[0], dict) else {}
    if viewer_scope(current_user) is not None:
        assert_row_visible(current_user, TABLE, first, supabase,
                           person_ids_for(supabase, current_user),
                           detail="That order line is not on the list.")
    entry = first.get("entry")
    stored = dict(entry) if isinstance(entry, dict) else {}
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if ("kind" in patch or "source" in patch) and ("kind" in stored or "source" in stored):
        raise HTTPException(status_code=400, detail="A line's kind and source cannot change.")
    merged = {**stored, **patch}
    try:
        await run_in_threadpool(
            lambda: supabase.table(TABLE).update({"entry": merged}).eq("record_id", record_id).execute()
        )
    except Exception as e:
        logger.error(f"update_order_line write error: {e}")
        raise server_error(e, "Updating order line")
    return _entry_of({"record_id": record_id, "entry": merged, "added_at": first.get("added_at")})


class RemoveLines(BaseModel):
    record_ids: List[str] = Field(..., max_length=MAX_LINES_PER_REQUEST)


def _entry_of(row: dict[str, Any]) -> dict[str, Any]:
    """The line as the page wants it: the stored entry, with the time it was added."""
    entry = dict(row.get("entry") or {})
    entry["record_id"] = row.get("record_id", entry.get("record_id"))
    entry["added_at"] = entry.get("added_at") or row.get("added_at")
    return entry


@router.get("")
@router.get("/")
async def list_order_lines(current_user: dict = Depends(get_current_user)):
    try:
        data = await run_in_threadpool(lambda: fetch_all_pages(
            lambda start, end: supabase.table(TABLE).select("*").order("added_at", desc=False).order("record_id").range(start, end).execute()
        ))
    except Exception as e:
        logger.error(f"list_order_lines error: {e}")
        raise server_error(e, "Loading order lines")
    if viewer_scope(current_user) is not None:
        data = filter_rows(current_user, TABLE, data, supabase, person_ids_for(supabase, current_user))
    return [_entry_of(row) for row in data]


@router.post("")
@router.post("/")
async def add_order_lines(body: AddLines, current_user: dict = Depends(get_current_user)):
    """Add lines; a line whose record is already on the list is left as it is (never doubled)."""
    if not body.entries:
        return {"added": 0}
    _check_record_ids(body.entries)
    if viewer_scope(current_user) is not None:
        person_ids = person_ids_for(supabase, current_user)
        for e in body.entries:
            if not row_visible(current_user, TABLE, {"entry": e.model_dump()}, supabase, person_ids):
                raise HTTPException(status_code=404, detail="Not found")
    who = current_user.get("email") or current_user.get("user_id") or ""
    payload = [{"record_id": e.record_id, "entry": e.model_dump(exclude_none=True), "added_by": who} for e in body.entries]
    try:
        result = await run_in_threadpool(lambda: supabase.table(TABLE).upsert(payload, on_conflict="record_id", ignore_duplicates=True).execute())
    except Exception as e:
        logger.error(f"add_order_lines error: {e}")
        raise server_error(e, "Creating order lines")
    return {"added": len(result.data or [])}


@router.post("/remove")
async def remove_order_lines(body: RemoveLines, current_user: dict = Depends(get_current_user)):
    """Remove the lines for these records (a line that is not there is not an error: the list already says what was asked)."""
    if not body.record_ids:
        return {"ok": True}
    try:
        doomed = list(body.record_ids)
        if viewer_scope(current_user) is not None:
            found = await run_in_threadpool(
                lambda: supabase.table(TABLE).select("*").in_("record_id", body.record_ids).execute()
            )
            person_ids = person_ids_for(supabase, current_user)
            found_rows = cast("list[dict]", found.data or [])
            doomed = [r.get("record_id") for r in found_rows
                      if row_visible(current_user, TABLE, r, supabase, person_ids)]
        if doomed:
            await run_in_threadpool(lambda: supabase.table(TABLE).delete().in_("record_id", doomed).execute())
    except Exception as e:
        logger.error(f"remove_order_lines error: {e}")
        raise server_error(e, "Deleting order lines")
    return {"ok": True}


@router.delete("")
@router.delete("/")
async def clear_order_list(current_user: dict = Depends(get_current_user)):
    """Empty the whole list (after the order has been placed) — a scoped clear
    empties the viewer's slice, never anyone else's lines."""
    try:
        if viewer_scope(current_user) is None:
            await run_in_threadpool(lambda: supabase.table(TABLE).delete().neq("record_id", "").execute())
        else:
            data = await run_in_threadpool(lambda: fetch_all_pages(
                lambda start, end: supabase.table(TABLE).select("*").range(start, end).execute()
            ))
            person_ids = person_ids_for(supabase, current_user)
            doomed = [r.get("record_id") for r in data
                      if row_visible(current_user, TABLE, r, supabase, person_ids)]
            if doomed:
                await run_in_threadpool(lambda: supabase.table(TABLE).delete().in_("record_id", doomed).execute())
    except Exception as e:
        logger.error(f"clear_order_list error: {e}")
        raise server_error(e, "Clearing order list")
    return {"ok": True}
