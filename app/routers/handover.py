from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Any
from app.supabase_client import supabase
import logging

router = APIRouter(tags=["Shift Handover"])
logger = logging.getLogger(__name__)

class HandoverCreate(BaseModel):
    handover_date: str
    shift: str
    outgoing_supervisor: str
    incoming_supervisor: Optional[str] = None
    section: Optional[str] = None
    equipment_summary: Optional[List[Any]] = []
    completed_work: Optional[str] = None
    outstanding_work: Optional[str] = None
    safety_concerns: Optional[str] = None
    environmental_issues: Optional[str] = None
    production_notes: Optional[str] = None
    general_notes: Optional[str] = None

class HandoverUpdate(BaseModel):
    incoming_supervisor: Optional[str] = None
    section: Optional[str] = None
    equipment_summary: Optional[List[Any]] = None
    completed_work: Optional[str] = None
    outstanding_work: Optional[str] = None
    safety_concerns: Optional[str] = None
    environmental_issues: Optional[str] = None
    production_notes: Optional[str] = None
    general_notes: Optional[str] = None
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None

@router.get("")
@router.get("/")
async def get_handovers(shift: Optional[str] = None, section: Optional[str] = None):
    try:
        q = supabase.table("shift_handovers").select("*").order("created_at", desc=True)
        if shift:   q = q.eq("shift", shift)
        if section: q = q.eq("section", section)
        return (q.execute()).data or []
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/{h_id}")
async def get_handover(h_id: int):
    r = supabase.table("shift_handovers").select("*").eq("id", h_id).execute()
    if not r.data:
        raise HTTPException(404, "Handover not found")
    return r.data[0]

@router.post("")
@router.post("/")
async def create_handover(data: HandoverCreate):
    try:
        r = supabase.table("shift_handovers").insert(data.dict(exclude_none=True)).execute()
        if not r.data:
            raise HTTPException(500, "Insert failed")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@router.patch("/{h_id}")
async def update_handover(h_id: int, data: HandoverUpdate):
    payload = {k: v for k, v in data.dict().items() if v is not None}
    r = supabase.table("shift_handovers").update(payload).eq("id", h_id).execute()
    if not r.data:
        raise HTTPException(404, "Handover not found")
    return r.data[0]

@router.delete("/{h_id}")
async def delete_handover(h_id: int):
    supabase.table("shift_handovers").delete().eq("id", h_id).execute()
    return {"ok": True}
