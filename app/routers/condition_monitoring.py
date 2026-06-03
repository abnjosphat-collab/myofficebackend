from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.supabase_client import supabase
import logging

router = APIRouter(tags=["Condition Monitoring"])
logger = logging.getLogger(__name__)

class CMCreate(BaseModel):
    equipment_id: Optional[str] = None
    equipment_name: str
    component: Optional[str] = None
    monitoring_type: str
    sampled_date: str
    value: Optional[float] = None
    unit: Optional[str] = None
    iron_ppm: Optional[float] = None
    copper_ppm: Optional[float] = None
    lead_ppm: Optional[float] = None
    viscosity: Optional[float] = None
    water_pct: Optional[float] = None
    result: str = "normal"
    lab_reference: Optional[str] = None
    technician: Optional[str] = None
    notes: Optional[str] = None

class CMUpdate(BaseModel):
    equipment_name: Optional[str] = None
    component: Optional[str] = None
    monitoring_type: Optional[str] = None
    sampled_date: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    iron_ppm: Optional[float] = None
    copper_ppm: Optional[float] = None
    lead_ppm: Optional[float] = None
    viscosity: Optional[float] = None
    water_pct: Optional[float] = None
    result: Optional[str] = None
    lab_reference: Optional[str] = None
    technician: Optional[str] = None
    notes: Optional[str] = None

@router.get("")
@router.get("/")
async def get_readings(monitoring_type: Optional[str] = None, result: Optional[str] = None, equipment_id: Optional[str] = None):
    try:
        q = supabase.table("condition_monitoring").select("*").order("sampled_date", desc=True)
        if monitoring_type: q = q.eq("monitoring_type", monitoring_type)
        if result:          q = q.eq("result", result)
        if equipment_id:    q = q.eq("equipment_id", equipment_id)
        return (q.execute()).data or []
    except Exception as e:
        raise HTTPException(500, str(e))

@router.post("")
@router.post("/")
async def create_reading(data: CMCreate):
    try:
        r = supabase.table("condition_monitoring").insert(data.dict(exclude_none=True)).execute()
        if not r.data:
            raise HTTPException(500, "Insert failed")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@router.patch("/{r_id}")
async def update_reading(r_id: int, data: CMUpdate):
    payload = {k: v for k, v in data.dict().items() if v is not None}
    r = supabase.table("condition_monitoring").update(payload).eq("id", r_id).execute()
    if not r.data:
        raise HTTPException(404, "Reading not found")
    return r.data[0]

@router.delete("/{r_id}")
async def delete_reading(r_id: int):
    supabase.table("condition_monitoring").delete().eq("id", r_id).execute()
    return {"ok": True}
