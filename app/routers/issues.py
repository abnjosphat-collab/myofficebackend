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
from pydantic import BaseModel, Field
import logging

from app.crud_router import CrudRouter
from app.supabase_client import supabase, rows
from app.auth import get_current_user

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


@router.get("/stats/summary", dependencies=[Depends(get_current_user)])
async def get_stats():
    try:
        response = supabase.table("stock_issues").select("issued_at, recipient_name").execute()
        records = rows(response)
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
