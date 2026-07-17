from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Any
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging

router = APIRouter(tags=["Contractors"])
logger = logging.getLogger(__name__)

class ContractorCreate(BaseModel):
    company_name: str
    trade: str
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    certifications: Optional[List[Any]] = []
    insurance_expiry: Optional[str] = None
    contract_start: Optional[str] = None
    contract_end: Optional[str] = None
    status: str = "active"
    performance_rating: Optional[int] = None
    notes: Optional[str] = None

class ContractorUpdate(BaseModel):
    company_name: Optional[str] = None
    trade: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    certifications: Optional[List[Any]] = None
    insurance_expiry: Optional[str] = None
    contract_start: Optional[str] = None
    contract_end: Optional[str] = None
    status: Optional[str] = None
    performance_rating: Optional[int] = None
    notes: Optional[str] = None

class ContractorJobCreate(BaseModel):
    contractor_id: int
    job_title: str
    equipment_name: Optional[str] = None
    scope: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    cost: Optional[float] = None
    currency: str = "USD"
    status: str = "planned"
    notes: Optional[str] = None

class ContractorJobUpdate(BaseModel):
    job_title: Optional[str] = None
    equipment_name: Optional[str] = None
    scope: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    cost: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = None

@router.get("")
@router.get("/")
async def get_contractors(status: Optional[str] = None, trade: Optional[str] = None):
    try:
        q = supabase.table("contractors").select("*").order("company_name")
        if status: q = q.eq("status", status)
        if trade:  q = q.eq("trade", trade)
        return (q.execute()).data or []
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/{c_id}")
async def get_contractor(c_id: int):
    r = supabase.table("contractors").select("*").eq("id", c_id).execute()
    if not r.data:
        raise HTTPException(404, "Contractor not found")
    contractor = r.data[0]
    jobs = supabase.table("contractor_jobs").select("*").eq("contractor_id", c_id).execute()
    contractor["jobs"] = jobs.data or []
    return contractor

@router.post("")
@router.post("/")
async def create_contractor(data: ContractorCreate, current_user: dict = Depends(get_current_user)):
    try:
        r = supabase.table("contractors").insert(data.dict(exclude_none=True)).execute()
        if not r.data:
            raise HTTPException(500, "Insert failed")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@router.patch("/{c_id}")
async def update_contractor(c_id: int, data: ContractorUpdate, current_user: dict = Depends(get_current_user)):
    payload = {k: v for k, v in data.dict().items() if v is not None}
    r = supabase.table("contractors").update(payload).eq("id", c_id).execute()
    if not r.data:
        raise HTTPException(404, "Contractor not found")
    return r.data[0]

@router.delete("/{c_id}")
async def delete_contractor(c_id: int, current_user: dict = Depends(require_role('manager'))):
    supabase.table("contractors").delete().eq("id", c_id).execute()
    return {"ok": True}

# ─── Contractor jobs ──────────────────────────────────────────────────────────

@router.get("/jobs/all")
async def get_all_jobs(status: Optional[str] = None):
    q = supabase.table("contractor_jobs").select("*, contractors(company_name, trade)").order("start_date", desc=True)
    if status: q = q.eq("status", status)
    return (q.execute()).data or []

@router.post("/jobs")
async def create_job(data: ContractorJobCreate, current_user: dict = Depends(get_current_user)):
    try:
        r = supabase.table("contractor_jobs").insert(data.dict(exclude_none=True)).execute()
        if not r.data:
            raise HTTPException(500, "Insert failed")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@router.patch("/jobs/{j_id}")
async def update_job(j_id: int, data: ContractorJobUpdate, current_user: dict = Depends(get_current_user)):
    payload = {k: v for k, v in data.dict().items() if v is not None}
    r = supabase.table("contractor_jobs").update(payload).eq("id", j_id).execute()
    if not r.data:
        raise HTTPException(404, "Job not found")
    return r.data[0]

@router.delete("/jobs/{j_id}")
async def delete_job(j_id: int, current_user: dict = Depends(require_role('manager'))):
    supabase.table("contractor_jobs").delete().eq("id", j_id).execute()
    return {"ok": True}
