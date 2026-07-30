from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# ============= Pydantic Models (match SQL) =============
class RequisitionItemCreate(BaseModel):
    description: str
    cost_per_unit: float
    quantity: int
    reason: Optional[str] = None

class RequisitionCreate(BaseModel):
    date: date
    requester: str
    section: str
    required_for: Optional[str] = None
    priority: str
    status: str
    requisition_number: str
    notes: Optional[str] = None
    items: List[RequisitionItemCreate]

class RequisitionUpdate(BaseModel):
    date: Optional[date] = None
    requester: Optional[str] = None
    section: Optional[str] = None
    required_for: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    requisition_number: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[RequisitionItemCreate]] = None

# ============= HEALTH CHECK =============
@router.get("/health")
async def health_check():
    try:
        supabase.table("requisitions").select("id").limit(1).execute()
        return {
            "status": "healthy",
            "service": "requisitions",
            "database": "connected",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "service": "requisitions",
            "database": "disconnected",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }

# ============= CREATE (handles both / and no trailing slash) =============
@router.post("")
@router.post("/")
async def create_requisition(requisition: RequisitionCreate, request: Request = None, current_user: dict = Depends(get_current_user)):
    logger.info(f"Creating requisition {requisition.requisition_number} for {requisition.requester} ({len(requisition.items)} items)")

    try:
        # Check unique requisition_number
        existing = supabase.table("requisitions").select("id").eq("requisition_number", requisition.requisition_number).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail=f"Requisition number '{requisition.requisition_number}' already exists")

        now = datetime.utcnow().isoformat()
        requisition_data = {
            "date": requisition.date.isoformat(),
            "requester": requisition.requester,
            "section": requisition.section,
            "required_for": requisition.required_for,
            "priority": requisition.priority,
            "status": requisition.status,
            "requisition_number": requisition.requisition_number,
            "notes": requisition.notes,
            "created_at": now,
            "updated_at": now
        }

        req_response = supabase.table("requisitions").insert(requisition_data).execute()
        if not req_response.data:
            raise HTTPException(status_code=500, detail="Failed to create requisition")

        new_requisition = req_response.data[0]
        requisition_id = new_requisition['id']

        if requisition.items:
            items_data = [
                {
                    "requisition_id": requisition_id,
                    "description": item.description,
                    "cost_per_unit": item.cost_per_unit,
                    "quantity": item.quantity,
                    "reason": item.reason,
                    "created_at": now
                }
                for item in requisition.items
            ]
            items_response = supabase.table("requisition_items").insert(items_data).execute()
            new_requisition['requisition_items'] = items_response.data or []
        else:
            new_requisition['requisition_items'] = []

        # Add line number for UI convenience
        all_reqs = supabase.table("requisitions").select("id").execute()
        new_requisition['line_number'] = len(all_reqs.data)

        logger.info(f"Successfully created requisition {requisition_id}")
        return new_requisition

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating requisition: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= GET ALL (handles both / and no trailing slash) =============
@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def get_requisitions(
    request: Request = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    section: Optional[str] = None,
    requester: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None
):
    try:
        query = supabase.table("requisitions").select("*, requisition_items(*)")

        if status and status != 'all':
            query = query.eq("status", status)
        if priority and priority != 'all':
            query = query.eq("priority", priority)
        if section and section != 'all':
            query = query.eq("section", section)
        if requester and requester != 'all':
            query = query.eq("requester", requester)
        if date_from:
            query = query.gte("date", date_from.isoformat())
        if date_to:
            query = query.lte("date", date_to.isoformat())

        response = query.order("created_at", desc=True).execute()
        requisitions = response.data or []

        for idx, req in enumerate(requisitions, 1):
            req['line_number'] = idx

        return requisitions

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= GET SINGLE =============
@router.get("/{requisition_id}", dependencies=[Depends(get_current_user)])
async def get_requisition(requisition_id: int):
    try:
        response = supabase.table("requisitions").select("*, requisition_items(*)").eq("id", requisition_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Requisition not found")
        requisition = response.data[0]
        all_reqs = supabase.table("requisitions").select("id").execute()
        for idx, req in enumerate(sorted(all_reqs.data, key=lambda x: x['id']), 1):
            if req['id'] == requisition_id:
                requisition['line_number'] = idx
                break
        return requisition
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= UPDATE =============
@router.patch("/{requisition_id}")
async def update_requisition(requisition_id: int, update: RequisitionUpdate, current_user: dict = Depends(get_current_user)):
    try:
        existing = supabase.table("requisitions").select("*").eq("id", requisition_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Requisition not found")

        if update.requisition_number:
            conflict = supabase.table("requisitions").select("id").eq("requisition_number", update.requisition_number).neq("id", requisition_id).execute()
            if conflict.data:
                raise HTTPException(status_code=400, detail=f"Requisition number '{update.requisition_number}' already exists")

        # exclude_unset, not a None-filter: an explicitly-sent null must clear the
        # field (e.g. required_for, notes), not be silently dropped like the old
        # per-field `is not None` checks here did. See work_orders (backend edec24a).
        update_data = update.dict(exclude_unset=True)
        update_data.pop('items', None)  # items is a separate table, handled below
        if update_data.get('date') is not None:
            update_data['date'] = update_data['date'].isoformat()

        if update_data:
            update_data['updated_at'] = datetime.utcnow().isoformat()
            supabase.table("requisitions").update(update_data).eq("id", requisition_id).execute()

        if update.items is not None:
            # Replace items
            supabase.table("requisition_items").delete().eq("requisition_id", requisition_id).execute()
            if update.items:
                now = datetime.utcnow().isoformat()
                items_data = [
                    {
                        "requisition_id": requisition_id,
                        "description": item.description,
                        "cost_per_unit": item.cost_per_unit,
                        "quantity": item.quantity,
                        "reason": item.reason,
                        "created_at": now
                    }
                    for item in update.items
                ]
                supabase.table("requisition_items").insert(items_data).execute()

        response = supabase.table("requisitions").select("*, requisition_items(*)").eq("id", requisition_id).execute()
        return response.data[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= DELETE =============
@router.delete("/{requisition_id}")
async def delete_requisition(requisition_id: int, current_user: dict = Depends(require_role('manager'))):
    try:
        existing = supabase.table("requisitions").select("*").eq("id", requisition_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Requisition not found")
        supabase.table("requisition_items").delete().eq("requisition_id", requisition_id).execute()
        supabase.table("requisitions").delete().eq("id", requisition_id).execute()
        return {"success": True, "message": "Requisition deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= DAILY TOTAL =============
@router.get("/daily-total/{date}", dependencies=[Depends(get_current_user)])
async def get_daily_total(date: date):
    try:
        reqs = supabase.table("requisitions").select("id").eq("date", date.isoformat()).execute()
        ids = [r['id'] for r in reqs.data] if reqs.data else []
        if not ids:
            return {"date": date.isoformat(), "total": 0}
        items = supabase.table("requisition_items").select("cost_per_unit, quantity").in_("requisition_id", ids).execute()
        total = sum(item['cost_per_unit'] * item['quantity'] for item in items.data) if items.data else 0
        return {"date": date.isoformat(), "total": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= STATISTICS =============
@router.get("/stats/summary", dependencies=[Depends(get_current_user)])
async def get_stats():
    try:
        reqs = supabase.table("requisitions").select("id, status, section").execute()
        items = supabase.table("requisition_items").select("cost_per_unit, quantity").execute()
        total_cost = sum(item['cost_per_unit'] * item['quantity'] for item in items.data) if items.data else 0
        status_counts = {}
        section_counts = {}
        for r in reqs.data or []:
            status_counts[r['status']] = status_counts.get(r['status'], 0) + 1
            section_counts[r['section']] = section_counts.get(r['section'], 0) + 1
        return {
            "total_requisitions": len(reqs.data or []),
            "total_cost": round(total_cost, 2),
            "status_breakdown": status_counts,
            "section_breakdown": section_counts
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= TEST ENDPOINT =============
@router.get("/test")
async def test_endpoint():
    return {
        "status": "ok",
        "message": "Requisitions router is working",
        "timestamp": datetime.utcnow().isoformat()
    }