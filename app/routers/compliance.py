from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from datetime import date
import logging

router = APIRouter(tags=["Compliance Register"])
logger = logging.getLogger(__name__)

def _compute_status(expiry: Optional[str]) -> str:
    if not expiry:
        return "current"
    try:
        days = (date.fromisoformat(expiry) - date.today()).days
        if days < 0:   return "overdue"
        if days <= 30: return "due_soon"
        return "current"
    except:
        return "current"

class ComplianceCreate(BaseModel):
    equipment_id: Optional[str] = None
    equipment_name: str
    inspection_type: str
    regulatory_body: Optional[str] = None
    certificate_no: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: str
    responsible: Optional[str] = None
    inspector: Optional[str] = None
    document_url: Optional[str] = None
    notes: Optional[str] = None

class ComplianceUpdate(BaseModel):
    equipment_name: Optional[str] = None
    inspection_type: Optional[str] = None
    regulatory_body: Optional[str] = None
    certificate_no: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
    status: Optional[str] = None
    responsible: Optional[str] = None
    inspector: Optional[str] = None
    document_url: Optional[str] = None
    notes: Optional[str] = None

@router.get("")
@router.get("/")
async def get_compliance(status: Optional[str] = None):
    try:
        r = supabase.table("compliance_register").select("*").order("expiry_date").execute()
        items = r.data or []
        # Auto-refresh computed status
        for item in items:
            item["status"] = _compute_status(item.get("expiry_date"))
        if status:
            items = [i for i in items if i["status"] == status]
        return items
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/{c_id}")
async def get_compliance_item(c_id: int):
    r = supabase.table("compliance_register").select("*").eq("id", c_id).execute()
    if not r.data:
        raise HTTPException(404, "Item not found")
    item = r.data[0]
    item["status"] = _compute_status(item.get("expiry_date"))
    return item

@router.post("")
@router.post("/")
async def create_compliance(data: ComplianceCreate, current_user: dict = Depends(get_current_user)):
    try:
        payload = data.dict(exclude_none=True)
        payload["status"] = _compute_status(data.expiry_date)
        r = supabase.table("compliance_register").insert(payload).execute()
        if not r.data:
            raise HTTPException(500, "Insert failed")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@router.patch("/{c_id}")
async def update_compliance(c_id: int, data: ComplianceUpdate, current_user: dict = Depends(get_current_user)):
    payload = {k: v for k, v in data.dict().items() if v is not None}
    if "expiry_date" in payload:
        payload["status"] = _compute_status(payload["expiry_date"])
    r = supabase.table("compliance_register").update(payload).eq("id", c_id).execute()
    if not r.data:
        raise HTTPException(404, "Item not found")
    return r.data[0]

@router.delete("/{c_id}")
async def delete_compliance(c_id: int, current_user: dict = Depends(require_role('manager'))):
    supabase.table("compliance_register").delete().eq("id", c_id).execute()
    return {"ok": True}
