from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Any
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging

router = APIRouter(tags=["Job Cards"])
logger = logging.getLogger(__name__)

class JobCardCreate(BaseModel):
    job_no: str
    title: str
    equipment_id: Optional[str] = None
    equipment_name: Optional[str] = None
    type: str = "corrective"
    priority: str = "medium"
    status: str = "open"
    description: Optional[str] = None
    tasks: Optional[List[Any]] = []
    parts_used: Optional[List[Any]] = []
    labour_hours: Optional[float] = 0
    assigned_to: Optional[str] = None
    supervisor: Optional[str] = None
    section: Optional[str] = None
    scheduled_date: Optional[str] = None
    notes: Optional[str] = None
    created_by: Optional[str] = None

class JobCardUpdate(BaseModel):
    title: Optional[str] = None
    equipment_id: Optional[str] = None
    equipment_name: Optional[str] = None
    type: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    tasks: Optional[List[Any]] = None
    parts_used: Optional[List[Any]] = None
    labour_hours: Optional[float] = None
    assigned_to: Optional[str] = None
    supervisor: Optional[str] = None
    section: Optional[str] = None
    scheduled_date: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    sign_off_by: Optional[str] = None
    sign_off_at: Optional[str] = None
    notes: Optional[str] = None

@router.get("")
@router.get("/")
async def get_job_cards(status: Optional[str] = None, priority: Optional[str] = None, section: Optional[str] = None):
    try:
        q = supabase.table("job_cards").select("*").order("created_at", desc=True)
        if status:   q = q.eq("status", status)
        if priority: q = q.eq("priority", priority)
        if section:  q = q.eq("section", section)
        return (q.execute()).data or []
    except Exception as e:
        logger.error(f"get_job_cards: {e}")
        raise HTTPException(500, str(e))

@router.get("/{jc_id}")
async def get_job_card(jc_id: int):
    r = supabase.table("job_cards").select("*").eq("id", jc_id).execute()
    if not r.data:
        raise HTTPException(404, "Job card not found")
    return r.data[0]

@router.post("")
@router.post("/")
async def create_job_card(data: JobCardCreate, current_user: dict = Depends(get_current_user)):
    try:
        r = supabase.table("job_cards").insert(data.dict(exclude_none=True)).execute()
        if not r.data:
            raise HTTPException(500, "Insert returned no data")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_job_card: {e}")
        raise HTTPException(500, str(e))

@router.patch("/{jc_id}")
async def update_job_card(jc_id: int, data: JobCardUpdate, current_user: dict = Depends(get_current_user)):
    # exclude_unset, not a None-filter: an explicitly-sent null must clear the
    # field, not be silently dropped. See work_orders (backend edec24a).
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(400, "No fields to update")
    r = supabase.table("job_cards").update(payload).eq("id", jc_id).execute()
    if not r.data:
        raise HTTPException(404, "Job card not found")
    return r.data[0]

@router.delete("/{jc_id}")
async def delete_job_card(jc_id: int, current_user: dict = Depends(require_role('manager'))):
    supabase.table("job_cards").delete().eq("id", jc_id).execute()
    return {"ok": True}
