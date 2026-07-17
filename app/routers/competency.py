from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging

router = APIRouter(tags=["Competency Matrix"])
logger = logging.getLogger(__name__)

class CompetencyCreate(BaseModel):
    employee_id: str
    employee_name: str
    trade: Optional[str] = None
    equipment_type: str
    skill_area: str
    skill_level: int = 0
    certified: bool = False
    cert_date: Optional[str] = None
    cert_expiry: Optional[str] = None
    certified_by: Optional[str] = None
    notes: Optional[str] = None

class CompetencyUpdate(BaseModel):
    trade: Optional[str] = None
    equipment_type: Optional[str] = None
    skill_area: Optional[str] = None
    skill_level: Optional[int] = None
    certified: Optional[bool] = None
    cert_date: Optional[str] = None
    cert_expiry: Optional[str] = None
    certified_by: Optional[str] = None
    notes: Optional[str] = None

@router.get("")
@router.get("/")
async def get_competencies(employee_id: Optional[str] = None, trade: Optional[str] = None, equipment_type: Optional[str] = None):
    try:
        q = supabase.table("competency_matrix").select("*").order("employee_name")
        if employee_id:    q = q.eq("employee_id", employee_id)
        if trade:          q = q.eq("trade", trade)
        if equipment_type: q = q.eq("equipment_type", equipment_type)
        return (q.execute()).data or []
    except Exception as e:
        raise HTTPException(500, str(e))

@router.post("")
@router.post("/")
async def create_competency(data: CompetencyCreate, current_user: dict = Depends(get_current_user)):
    try:
        r = supabase.table("competency_matrix").insert(data.dict(exclude_none=True)).execute()
        if not r.data:
            raise HTTPException(500, "Insert failed")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@router.patch("/{c_id}")
async def update_competency(c_id: int, data: CompetencyUpdate, current_user: dict = Depends(get_current_user)):
    payload = {k: v for k, v in data.dict().items() if v is not None}
    r = supabase.table("competency_matrix").update(payload).eq("id", c_id).execute()
    if not r.data:
        raise HTTPException(404, "Entry not found")
    return r.data[0]

@router.delete("/{c_id}")
async def delete_competency(c_id: int, current_user: dict = Depends(require_role('manager'))):
    supabase.table("competency_matrix").delete().eq("id", c_id).execute()
    return {"ok": True}
