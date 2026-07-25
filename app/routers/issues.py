"""
Stock Issues Router — record items issued to personnel
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date, timedelta
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

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

@router.get("", dependencies=[Depends(get_current_user)])
async def get_issues(
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    try:
        query = supabase.table("stock_issues").select("*")
        if search:
            query = query.or_(
                f"recipient_name.ilike.%{search}%,"
                f"recipient_id.ilike.%{search}%,"
                f"issued_by.ilike.%{search}%"
            )
        if date_from:
            query = query.gte("issued_at", date_from)
        if date_to:
            query = query.lte("issued_at", date_to + "T23:59:59")
        query = query.order("issued_at", desc=True).limit(limit).offset(offset)
        response = query.execute()
        return response.data or []
    except Exception as e:
        logger.error(f"Error fetching issues: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("", status_code=201)
async def create_issue(issue: StockIssueCreate, current_user: dict = Depends(get_current_user)):
    try:
        now = datetime.utcnow().isoformat()
        row = {
            "issued_at": issue.issued_at or now,
            "recipient_name": issue.recipient_name.strip(),
            "recipient_id": issue.recipient_id or None,
            "issued_by": issue.issued_by or None,
            "items": [item.dict() for item in issue.items],
            "notes": issue.notes or None,
            "created_at": now,
        }
        response = supabase.table("stock_issues").insert(row).execute()
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to record issue")
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating issue: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{issue_id}")
async def delete_issue(issue_id: int, current_user: dict = Depends(require_role('manager'))):
    try:
        existing = supabase.table("stock_issues").select("id").eq("id", issue_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Issue record not found")
        supabase.table("stock_issues").delete().eq("id", issue_id).execute()
        return {"message": "Deleted", "id": issue_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting issue: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats/summary", dependencies=[Depends(get_current_user)])
async def get_stats():
    try:
        response = supabase.table("stock_issues").select("issued_at, recipient_name").execute()
        records = response.data or []
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
        logger.error(f"Error fetching issue stats: {e}")
        return {"total": 0, "today": 0, "this_week": 0, "unique_recipients": 0}
