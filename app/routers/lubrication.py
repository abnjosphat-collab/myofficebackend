from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from datetime import date, timedelta
import logging

router = APIRouter(tags=["Lubrication"])
logger = logging.getLogger(__name__)

def _lube_status(next_due: Optional[str]) -> str:
    if not next_due:
        return "current"
    try:
        days = (date.fromisoformat(next_due) - date.today()).days
        if days < 0:   return "overdue"
        if days <= 7:  return "due_soon"
        return "current"
    except:
        return "current"

class LubeScheduleCreate(BaseModel):
    equipment_id: Optional[str] = None
    equipment_name: str
    lube_point: str
    lubricant_type: str
    lubricant_grade: Optional[str] = None
    quantity_litres: Optional[float] = None
    interval_hours: Optional[int] = None
    interval_days: Optional[int] = None
    last_done_date: Optional[str] = None
    last_done_hours: Optional[int] = None
    next_due_date: Optional[str] = None
    next_due_hours: Optional[int] = None
    section: Optional[str] = None

class LubeScheduleUpdate(BaseModel):
    equipment_name: Optional[str] = None
    lube_point: Optional[str] = None
    lubricant_type: Optional[str] = None
    lubricant_grade: Optional[str] = None
    quantity_litres: Optional[float] = None
    interval_hours: Optional[int] = None
    interval_days: Optional[int] = None
    last_done_date: Optional[str] = None
    last_done_hours: Optional[int] = None
    next_due_date: Optional[str] = None
    next_due_hours: Optional[int] = None
    section: Optional[str] = None

class LubeRecordCreate(BaseModel):
    schedule_id: Optional[int] = None
    equipment_name: str
    lube_point: str
    done_date: str
    done_hours: Optional[int] = None
    quantity_used: Optional[float] = None
    technician: Optional[str] = None
    notes: Optional[str] = None

@router.get("")
@router.get("/")
async def get_schedules(status: Optional[str] = None, section: Optional[str] = None):
    try:
        q = supabase.table("lube_schedules").select("*").order("next_due_date")
        if section: q = q.eq("section", section)
        items = (q.execute()).data or []
        for item in items:
            item["status"] = _lube_status(item.get("next_due_date"))
        if status:
            items = [i for i in items if i["status"] == status]
        return items
    except Exception as e:
        raise HTTPException(500, str(e))

@router.post("")
@router.post("/")
async def create_schedule(data: LubeScheduleCreate, current_user: dict = Depends(get_current_user)):
    try:
        payload = data.dict(exclude_none=True)
        payload["status"] = _lube_status(data.next_due_date)
        r = supabase.table("lube_schedules").insert(payload).execute()
        if not r.data:
            raise HTTPException(500, "Insert failed")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@router.patch("/{s_id}")
async def update_schedule(s_id: int, data: LubeScheduleUpdate, current_user: dict = Depends(get_current_user)):
    payload = {k: v for k, v in data.dict().items() if v is not None}
    if "next_due_date" in payload:
        payload["status"] = _lube_status(payload["next_due_date"])
    r = supabase.table("lube_schedules").update(payload).eq("id", s_id).execute()
    if not r.data:
        raise HTTPException(404, "Schedule not found")
    return r.data[0]

@router.delete("/{s_id}")
async def delete_schedule(s_id: int, current_user: dict = Depends(require_role('manager'))):
    supabase.table("lube_schedules").delete().eq("id", s_id).execute()
    return {"ok": True}

# ─── Lube records (history) ───────────────────────────────────────────────────

@router.get("/records")
async def get_lube_records(schedule_id: Optional[int] = None):
    q = supabase.table("lube_records").select("*").order("done_date", desc=True)
    if schedule_id:
        q = q.eq("schedule_id", schedule_id)
    return (q.execute()).data or []

@router.post("/records")
async def create_lube_record(data: LubeRecordCreate, current_user: dict = Depends(get_current_user)):
    try:
        r = supabase.table("lube_records").insert(data.dict(exclude_none=True)).execute()
        if not r.data:
            raise HTTPException(500, "Insert failed")
        # Update the parent schedule's last_done_date
        if data.schedule_id and data.done_date:
            supabase.table("lube_schedules").update({
                "last_done_date": data.done_date,
                "last_done_hours": data.done_hours,
            }).eq("id", data.schedule_id).execute()
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
