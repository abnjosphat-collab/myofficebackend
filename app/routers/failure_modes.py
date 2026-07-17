from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging

router = APIRouter(tags=["Failure Modes"])
logger = logging.getLogger(__name__)

class FMCreate(BaseModel):
    equipment_type: str
    equipment_name: Optional[str] = None
    component: str
    failure_mode: str
    failure_cause: Optional[str] = None
    symptoms: Optional[str] = None
    detection_method: Optional[str] = None
    corrective_action: Optional[str] = None
    preventive_action: Optional[str] = None
    severity: Optional[int] = None
    probability: Optional[int] = None
    detectability: Optional[int] = None
    occurrence_count: int = 0
    last_occurred: Optional[str] = None
    created_by: Optional[str] = None

class FMUpdate(BaseModel):
    equipment_type: Optional[str] = None
    equipment_name: Optional[str] = None
    component: Optional[str] = None
    failure_mode: Optional[str] = None
    failure_cause: Optional[str] = None
    symptoms: Optional[str] = None
    detection_method: Optional[str] = None
    corrective_action: Optional[str] = None
    preventive_action: Optional[str] = None
    severity: Optional[int] = None
    probability: Optional[int] = None
    detectability: Optional[int] = None
    occurrence_count: Optional[int] = None
    last_occurred: Optional[str] = None

@router.get("")
@router.get("/")
async def get_failure_modes(equipment_type: Optional[str] = None):
    try:
        q = supabase.table("failure_modes").select("*").order("equipment_type")
        if equipment_type: q = q.eq("equipment_type", equipment_type)
        return (q.execute()).data or []
    except Exception as e:
        raise HTTPException(500, str(e))

@router.post("")
@router.post("/")
async def create_failure_mode(data: FMCreate, current_user: dict = Depends(get_current_user)):
    try:
        r = supabase.table("failure_modes").insert(data.dict(exclude_none=True)).execute()
        if not r.data:
            raise HTTPException(500, "Insert failed")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@router.patch("/{fm_id}")
async def update_failure_mode(fm_id: int, data: FMUpdate, current_user: dict = Depends(get_current_user)):
    payload = {k: v for k, v in data.dict().items() if v is not None}
    r = supabase.table("failure_modes").update(payload).eq("id", fm_id).execute()
    if not r.data:
        raise HTTPException(404, "Failure mode not found")
    return r.data[0]

@router.delete("/{fm_id}")
async def delete_failure_mode(fm_id: int, current_user: dict = Depends(require_role('manager'))):
    supabase.table("failure_modes").delete().eq("id", fm_id).execute()
    return {"ok": True}
