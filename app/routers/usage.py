# app/routers/usage.py — server-side usage analytics ingest/read.
#
# Ingestion (POST) deliberately accepts anonymous callers: an unsigned-in visitor's
# interactions are exactly the data this exists to capture, alongside signed-in
# users'. Reading the aggregate log back out (GET) is manager+ only, since it's
# effectively an activity log of every user on the system.

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from app.supabase_client import supabase
from app.auth import get_current_user_optional, require_role
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_TYPES = {'module_open', 'page_view', 'search', 'feedback'}


class UsageEventIn(BaseModel):
    type: str
    ts: int  # epoch ms — matches the frontend's UsageEvent.ts
    session_id: str = Field(..., min_length=1, max_length=128)
    href: Optional[str] = None
    path: Optional[str] = None
    title: Optional[str] = None
    query: Optional[str] = None
    results: Optional[int] = None
    rating: Optional[int] = None
    text: Optional[str] = None
    dwell_ms: Optional[int] = None


class UsageEventBatch(BaseModel):
    events: List[UsageEventIn]


@router.post("/events")
async def ingest_events(batch: UsageEventBatch, current_user: Optional[dict] = Depends(get_current_user_optional)):
    rows = []
    for e in batch.events:
        if e.type not in ALLOWED_TYPES:
            continue
        rows.append({
            "ts": datetime.fromtimestamp(e.ts / 1000, tz=timezone.utc).isoformat(),
            "type": e.type,
            "session_id": e.session_id,
            "user_id": current_user["user_id"] if current_user else None,
            "user_email": current_user["email"] if current_user else None,
            "href": e.href,
            "path": e.path,
            "title": e.title,
            "query": e.query,
            "results": e.results,
            "rating": e.rating,
            "feedback_text": e.text,
            "dwell_ms": e.dwell_ms,
        })
    if not rows:
        return {"inserted": 0}
    try:
        supabase.table("usage_events").insert(rows).execute()
    except Exception as e:
        logger.error(f"Failed inserting usage events: {e}")
        raise HTTPException(status_code=500, detail="Failed to record usage events")
    return {"inserted": len(rows)}


@router.get("/events", dependencies=[Depends(require_role('manager'))])
async def list_events(since_days: int = 100, limit: int = 20000):
    since_days = min(max(since_days, 1), 400)
    limit = min(max(limit, 1), 50000)
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    try:
        response = (
            supabase.table("usage_events")
            .select("*")
            .gte("ts", since)
            .order("ts", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception as e:
        logger.error(f"Failed fetching usage events: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch usage events")
