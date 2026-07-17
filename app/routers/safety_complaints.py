# backend/app/routers/safety_complaints.py

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging
from datetime import datetime, date
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/safety-complaints", tags=["Safety Complaints"])

# =============== PYDANTIC MODELS ===============

class SafetyComplaintBase(BaseModel):
    date: str = Field(..., min_length=1)
    raised_by: Optional[str] = ""
    issue_raised: str = Field(..., min_length=5)
    category: Optional[str] = "General"
    priority: Optional[str] = "medium"
    section: Optional[str] = "General"
    location: Optional[str] = ""
    action_plan: Optional[str] = ""
    by_who: Optional[str] = ""
    by_when: Optional[str] = ""
    supervisor_name: Optional[str] = ""
    supervisor_signature: Optional[str] = ""
    date_closed: Optional[str] = None
    status: Optional[str] = "open"

    @validator("priority")
    def validate_priority(cls, v):
        allowed = {"low", "medium", "high", "critical"}
        if v and v.lower() not in allowed:
            raise ValueError(f"priority must be one of {allowed}")
        return v.lower() if v else "medium"

    @validator("status")
    def validate_status(cls, v):
        allowed = {"open", "in-progress", "closed", "overdue"}
        if v and v.lower() not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v.lower() if v else "open"


class SafetyComplaintCreate(SafetyComplaintBase):
    pass


class SafetyComplaintUpdate(BaseModel):
    date: Optional[str] = None
    raised_by: Optional[str] = None
    issue_raised: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    section: Optional[str] = None
    location: Optional[str] = None
    action_plan: Optional[str] = None
    by_who: Optional[str] = None
    by_when: Optional[str] = None
    supervisor_name: Optional[str] = None
    supervisor_signature: Optional[str] = None
    date_closed: Optional[str] = None
    status: Optional[str] = None


class SafetyComplaintResponse(SafetyComplaintBase):
    id: str
    submitted_at: str

    class Config:
        from_attributes = True


# =============== HELPERS ===============

def generate_id() -> str:
    return str(uuid.uuid4())


def map_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "date": row.get("date"),
        "raisedBy": row.get("raised_by", ""),
        "issueRaised": row.get("issue_raised"),
        "category": row.get("category", "General"),
        "priority": row.get("priority", "medium"),
        "section": row.get("section", "General"),
        "location": row.get("location", ""),
        "actionPlan": row.get("action_plan", ""),
        "byWho": row.get("by_who", ""),
        "byWhen": row.get("by_when", ""),
        "supervisorName": row.get("supervisor_name", ""),
        "supervisorSignature": row.get("supervisor_signature", ""),
        "dateClosed": row.get("date_closed"),
        "status": row.get("status", "open"),
        "submittedAt": row.get("submitted_at"),
    }


# =============== ENDPOINTS ===============

@router.get("")
@router.get("/")
async def list_complaints(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    section: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    try:
        q = supabase.table("safety_complaints").select("*").order("date", desc=True)
        if status:
            q = q.eq("status", status)
        if section:
            q = q.eq("section", section)
        if priority:
            q = q.eq("priority", priority)
        if category:
            q = q.eq("category", category)
        if from_date:
            q = q.gte("date", from_date)
        if to_date:
            q = q.lte("date", to_date)
        q = q.range(offset, offset + limit - 1)
        result = q.execute()
        rows = result.data or []
        mapped = [map_row(r) for r in rows]
        if search:
            s = search.lower()
            mapped = [
                r for r in mapped
                if s in (r.get("issueRaised") or "").lower()
                or s in (r.get("raisedBy") or "").lower()
                or s in (r.get("supervisorName") or "").lower()
                or s in (r.get("location") or "").lower()
                or s in (r.get("category") or "").lower()
            ]
        return mapped
    except Exception as e:
        logger.error(f"Error listing safety complaints: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{complaint_id}")
async def get_complaint(complaint_id: str):
    try:
        result = supabase.table("safety_complaints").select("*").eq("id", complaint_id).single().execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Complaint not found")
        return map_row(result.data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching complaint {complaint_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_complaint(payload: SafetyComplaintCreate, current_user: dict = Depends(get_current_user)):
    try:
        record = {
            "id": generate_id(),
            "date": payload.date,
            "raised_by": payload.raised_by or "",
            "issue_raised": payload.issue_raised,
            "category": payload.category or "General",
            "priority": payload.priority or "medium",
            "section": payload.section or "General",
            "location": payload.location or "",
            "action_plan": payload.action_plan or "",
            "by_who": payload.by_who or "",
            "by_when": payload.by_when or None,        # date column — must be None not ""
            "supervisor_name": payload.supervisor_name or "",
            "supervisor_signature": payload.supervisor_signature or "",
            "date_closed": payload.date_closed or None, # date column — must be None not ""
            "status": payload.status or "open",
            "submitted_at": datetime.utcnow().isoformat(),
        }
        result = supabase.table("safety_complaints").insert(record).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Insert returned no data")
        return map_row(result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating complaint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


_DATE_COLS = {"by_when", "date_closed", "date"}

@router.patch("/{complaint_id}")
async def update_complaint(complaint_id: str, payload: SafetyComplaintUpdate, current_user: dict = Depends(get_current_user)):
    try:
        raw = payload.dict(exclude_none=True)
        # Convert empty strings to None for date-typed columns so Postgres doesn't reject them
        updates = {k: (None if k in _DATE_COLS and v == "" else v) for k, v in raw.items()}
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        result = supabase.table("safety_complaints").update(updates).eq("id", complaint_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Complaint not found")
        return map_row(result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating complaint {complaint_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{complaint_id}", status_code=204)
async def delete_complaint(complaint_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        result = supabase.table("safety_complaints").delete().eq("id", complaint_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Complaint not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting complaint {complaint_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
