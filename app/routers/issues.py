"""
Stock Issues Router — record items issued to personnel

Migrated onto the shared CrudRouter (see app/crud_router.py) — list/create/delete
was plain CRUD, no computed fields, `items` is a nested Pydantic model list that
CrudRouter's `.dict()` already serializes recursively (same as the original
hand-written version's `[item.dict() for item in issue.items]`), no hook needed
for it.

Two things the original router had that this migration deliberately changes:
- `date_from`/`date_to` range filtering on GET was dropped — CrudRouter's generic
  `filters` only does equality matches, and the frontend (app/issues/useIssuesData.ts)
  never actually sends these params (fetches everything, filters client-side), so
  nothing live depended on it.
- No update endpoint existed before; CrudRouter always exposes one. Being able to
  fix a typo in an issued-items record is a reasonable, low-risk addition — still
  gated behind sign-in like every other endpoint here, nothing new is exposed.

/stats/summary is genuinely custom (an aggregation, not a CRUD verb) and stays
hand-added alongside the base, same pattern as contractors.py.
"""
from datetime import date, timedelta
from typing import List, Optional

from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
import logging

from app.crud_router import CrudRouter
from app.supabase_client import supabase, one_row
from app.auth import get_current_user, require_role
from app.db_helpers import fetch_all_pages
from app.stock_levels import adjust_stock, item_deltas

logger = logging.getLogger(__name__)

# Run this SQL in Supabase before using this router:
#
#   CREATE TABLE stock_issues (
#     id SERIAL PRIMARY KEY,
#     issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
#     recipient_name TEXT NOT NULL,
#     recipient_id TEXT,
#     issued_by TEXT,
#     items JSONB NOT NULL DEFAULT '[]',
#     notes TEXT,
#     created_at TIMESTAMPTZ DEFAULT NOW()
#   );


class IssueItem(BaseModel):
    stock_code: Optional[str] = None
    description: str = Field(..., min_length=1)
    qty: float = Field(1, gt=0)
    unit: Optional[str] = "UN"
    unit_price: Optional[float] = Field(None, ge=0)


class StockIssueCreate(BaseModel):
    issued_at: Optional[str] = None
    recipient_name: str = Field(..., min_length=1)
    recipient_id: Optional[str] = None
    issued_by: Optional[str] = None
    items: List[IssueItem] = Field(..., min_length=1)
    notes: Optional[str] = None


class StockIssueUpdate(BaseModel):
    issued_at: Optional[str] = None
    recipient_name: Optional[str] = Field(None, min_length=1)
    recipient_id: Optional[str] = None
    issued_by: Optional[str] = None
    items: Optional[List[IssueItem]] = None
    notes: Optional[str] = None


def _clean_issue_write(data: dict) -> dict:
    """Mirrors the original router's write-time normalization: trim recipient_name,
    collapse an empty-string optional field back to None."""
    if isinstance(data.get("recipient_name"), str):
        data["recipient_name"] = data["recipient_name"].strip()
    for field in ("recipient_id", "issued_by", "notes"):
        if data.get(field) == "":
            data[field] = None
    return data


router = CrudRouter(
    "stock_issues", StockIssueCreate, StockIssueUpdate,
    order_by="issued_at", order_desc=True,
    search_columns=["recipient_name", "recipient_id", "issued_by"],
    default_limit=500,
    not_found="Issue record not found",
    before_create=_clean_issue_write,
    before_update=_clean_issue_write,
).router

# Issuing stock takes it off the spare's quantity (app/stock_levels.py). The generic create, update and delete routes cannot do that,
# so those three are replaced here with versions that also move the stock; list stays the generic one.
router.routes[:] = [
    r for r in router.routes
    if not (getattr(r, "path", None) in ("", "/") and "POST" in getattr(r, "methods", set()))
    and not (getattr(r, "path", None) == "/{item_id}" and getattr(r, "methods", set()) & {"PATCH", "DELETE"})
]


def _items_of(row: dict) -> list[dict]:
    items = row.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _with_stock(row: dict, report: dict) -> dict:
    return {**row, "stock": report["stock"], "stock_warnings": report["warnings"]}


async def _create_issue(data: StockIssueCreate):
    try:
        payload = _clean_issue_write(data.dict(exclude_none=True))
        created = one_row(await run_in_threadpool(lambda: supabase.table("stock_issues").insert(payload).execute()))
        if created is None:
            raise HTTPException(status_code=500, detail="Insert failed")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[stock_issues] create failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    report = await run_in_threadpool(lambda: adjust_stock(item_deltas(_items_of(created)), issue=True))
    return _with_stock(created, report)


async def _update_issue(item_id: int, data: StockIssueUpdate):
    payload = _clean_issue_write(data.dict(exclude_unset=True))
    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        before = one_row(await run_in_threadpool(lambda: supabase.table("stock_issues").select("*").eq("id", item_id).execute()))
        if before is None:
            raise HTTPException(status_code=404, detail="Issue record not found")
        updated = one_row(await run_in_threadpool(lambda: supabase.table("stock_issues").update(payload).eq("id", item_id).execute()))
        if updated is None:
            raise HTTPException(status_code=404, detail="Issue record not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[stock_issues] update failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    report = {"stock": [], "warnings": []}
    if "items" in payload:
        # Only the difference moves: more issued than before takes more off, less puts the rest back.
        old, new = item_deltas(_items_of(before)), item_deltas(_items_of(updated))
        extra = {c: new.get(c, 0) - old.get(c, 0) for c in set(old) | set(new)}
        take = {c: q for c, q in extra.items() if q > 0}
        give = {c: -q for c, q in extra.items() if q < 0}
        taken = await run_in_threadpool(lambda: adjust_stock(take, issue=True))
        given = await run_in_threadpool(lambda: adjust_stock(give, issue=False))
        report = {"stock": taken["stock"] + given["stock"], "warnings": taken["warnings"] + given["warnings"]}
    return _with_stock(updated, report)


async def _delete_issue(item_id: int, current_user: dict = Depends(require_role("manager"))):
    try:
        before = one_row(await run_in_threadpool(lambda: supabase.table("stock_issues").select("*").eq("id", item_id).execute()))
        await run_in_threadpool(lambda: supabase.table("stock_issues").delete().eq("id", item_id).execute())
    except Exception as e:
        logger.error(f"[stock_issues] delete failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    # Deleting an issue (a record made in error) puts what it took back on the shelf.
    report = await run_in_threadpool(lambda: adjust_stock(item_deltas(_items_of(before or {})), issue=False)) if before else {"stock": [], "warnings": []}
    return {"ok": True, "stock": report["stock"], "stock_warnings": report["warnings"]}


for _path in ("", "/"):
    router.add_api_route(_path, _create_issue, methods=["POST"], dependencies=[Depends(get_current_user)])
router.add_api_route("/{item_id}", _update_issue, methods=["PATCH"], dependencies=[Depends(get_current_user)])
router.add_api_route("/{item_id}", _delete_issue, methods=["DELETE"])


@router.get("/stats/summary", dependencies=[Depends(get_current_user)])
async def get_stats():
    try:
        # Every page: PostgREST cuts a plain read at 1,000 rows, which would make the totals wrong once there are more issues than that.
        records = await run_in_threadpool(lambda: fetch_all_pages(
            lambda start, end: supabase.table("stock_issues").select("issued_at, recipient_name").order("id").range(start, end).execute()
        ))
        today_str = date.today().isoformat()
        week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        today_count = sum(1 for r in records if (r.get("issued_at") or "").startswith(today_str))
        week_count = sum(1 for r in records if (r.get("issued_at") or "") >= week_start)
        recipients = len(set(r.get("recipient_name", "") for r in records if r.get("recipient_name")))
        return {
            "total": len(records),
            "today": today_count,
            "this_week": week_count,
            "unique_recipients": recipients,
        }
    except Exception as e:
        # Was returning a fake all-zero 200 here — silently indistinguishable from a
        # genuinely quiet week. Raise instead, matching this project's standard
        # (backend/docs/ENGINEERING_STANDARDS.md).
        logger.error(f"Error fetching issue stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to load issue stats")
