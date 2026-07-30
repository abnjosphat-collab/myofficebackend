# leaves.py – simplified, no department/manager, with error logging
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import date, datetime
from app.supabase_client import supabase
from app.auth import require_role, get_current_user
from app.db_helpers import get_or_404
import logging
import traceback

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------- Models ----------
class LeaveCreate(BaseModel):
    employee_id: str = Field(...)
    employee_name: str = Field(...)
    position: str = Field(...)
    contact_number: str = Field(...)
    leave_type: str = Field(...)
    start_date: date = Field(...)
    end_date: date = Field(...)
    reason: str = Field(..., min_length=1)
    emergency_contact: Optional[str] = None
    handover_to: Optional[str] = None
    notes: Optional[str] = None

    @validator('end_date')
    def end_date_after_start_date(cls, v, values):
        if 'start_date' in values and v < values['start_date']:
            raise ValueError('End date must be after start date')
        return v

class LeaveUpdate(BaseModel):
    leave_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    reason: Optional[str] = None
    emergency_contact: Optional[str] = None
    handover_to: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None

    @validator('end_date')
    def end_date_after_start_date(cls, v, values):
        if v is not None and values.get('start_date') is not None and v < values['start_date']:
            raise ValueError('End date must be after start date')
        return v

class LeaveResponse(BaseModel):
    id: int
    employee_id: str
    employee_name: str
    position: str
    contact_number: str
    leave_type: str
    start_date: date
    end_date: date
    total_days: int
    reason: str
    emergency_contact: Optional[str]
    handover_to: Optional[str]
    status: str
    applied_date: datetime
    updated_at: Optional[datetime]
    notes: Optional[str]

    class Config:
        from_attributes = True
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

# ---------- Helper ----------
def calculate_total_days(start_date: date, end_date: date) -> int:
    return (end_date - start_date).days + 1

def get_supabase_data(response):
    if hasattr(response, 'data'):
        return response.data
    return response

# ---------- POST create leave ----------
@router.post("", response_model=LeaveResponse)
@router.post("/", response_model=LeaveResponse)
async def create_leave(leave: LeaveCreate, current_user: dict = Depends(get_current_user)):
    try:
        total_days = calculate_total_days(leave.start_date, leave.end_date)

        data_to_insert = {
            "employee_id": leave.employee_id,
            "employee_name": leave.employee_name,
            "position": leave.position,
            "contact_number": leave.contact_number,
            "leave_type": leave.leave_type,
            "start_date": leave.start_date.isoformat(),
            "end_date": leave.end_date.isoformat(),
            "total_days": total_days,
            "reason": leave.reason,
            "emergency_contact": leave.emergency_contact,
            "handover_to": leave.handover_to,
            "notes": leave.notes,
            "status": "pending",
            "applied_date": datetime.utcnow().isoformat(),
        }

        result = supabase.table("leaves").insert(data_to_insert).execute()
        created = get_supabase_data(result)
        if not created:
            raise HTTPException(status_code=500, detail="No data returned after insertion")
        return created[0]
    except Exception as e:
        logger.error(f"Error creating leave: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error creating leave: {str(e)}")

# ---------- GET all leaves ----------
@router.get("", response_model=List[LeaveResponse], dependencies=[Depends(get_current_user)])
@router.get("/", response_model=List[LeaveResponse], dependencies=[Depends(get_current_user)])
async def get_leaves(status: Optional[str] = None, leave_type: Optional[str] = None):
    try:
        query = supabase.table("leaves").select("*")
        if status:
            query = query.eq("status", status)
        if leave_type:
            query = query.eq("leave_type", leave_type)
        query = query.order("applied_date", desc=True)
        response = query.execute()
        data = get_supabase_data(response)
        return data or []
    except Exception as e:
        logger.error(f"Error fetching leaves: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error fetching leaves: {str(e)}")

# ---------- GET leave stats ----------
@router.get("/stats/summary")  # deliberately open — aggregate counts, not individual records
async def get_leave_stats():
    try:
        response = supabase.table("leaves").select("*").execute()
        data = get_supabase_data(response) or []
        today = date.today().isoformat()
        total = len(data)
        pending = sum(1 for l in data if l.get('status') == 'pending')
        approved = sum(1 for l in data if l.get('status') == 'approved')
        rejected = sum(1 for l in data if l.get('status') == 'rejected')
        on_leave_now = sum(1 for l in data if l.get('status') == 'approved' and l.get('start_date') <= today <= l.get('end_date'))
        upcoming = sum(1 for l in data if l.get('status') == 'approved' and l.get('start_date') > today)
        return {
            "total": total,
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "on_leave_now": on_leave_now,
            "upcoming": upcoming
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}\n{traceback.format_exc()}")
        return {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "on_leave_now": 0, "upcoming": 0}

# ---------- GET leave by id ----------
@router.get("/{leave_id}", response_model=LeaveResponse, dependencies=[Depends(get_current_user)])
async def get_leave(leave_id: int):
    try:
        return get_or_404(supabase, "leaves", leave_id, detail="Leave not found")
    except Exception as e:
        logger.error(f"Error fetching leave {leave_id}: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ---------- PATCH update leave ----------
@router.patch("/{leave_id}", response_model=LeaveResponse)
async def update_leave(leave_id: int, updated: LeaveUpdate, authorization: Optional[str] = Header(None), current_user: dict = Depends(get_current_user)):
    # Any edit requires a signed-in user (current_user); approve/reject additionally
    # requires manager+ (checked below against the same Authorization header).
    if updated.status in ('approved', 'rejected'):
        approver = await require_role('manager')(authorization)
        logger.info(f"Leave approval '{updated.status}' by {approver['email']} (role: {approver['role']})")
    try:
        existing = get_or_404(supabase, "leaves", leave_id, detail=f"Leave with ID {leave_id} not found")

        data_to_update = updated.dict(exclude_unset=True)

        if 'start_date' in data_to_update or 'end_date' in data_to_update:
            start = data_to_update.get('start_date', date.fromisoformat(existing['start_date']))
            end = data_to_update.get('end_date', date.fromisoformat(existing['end_date']))
            data_to_update['total_days'] = calculate_total_days(start, end)

        if 'start_date' in data_to_update and isinstance(data_to_update['start_date'], date):
            data_to_update['start_date'] = data_to_update['start_date'].isoformat()
        if 'end_date' in data_to_update and isinstance(data_to_update['end_date'], date):
            data_to_update['end_date'] = data_to_update['end_date'].isoformat()

        if not data_to_update:
            return existing

        result = supabase.table("leaves").update(data_to_update).eq("id", leave_id).execute()
        updated_data = get_supabase_data(result)
        if not updated_data:
            fetch_resp = supabase.table("leaves").select("*").eq("id", leave_id).execute()
            fetched = get_supabase_data(fetch_resp)
            if not fetched:
                raise HTTPException(status_code=500, detail="No data returned after update")
            return fetched[0]
        return updated_data[0]
    except Exception as e:
        logger.error(f"Error updating leave {leave_id}: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error updating leave: {str(e)}")

# ---------- DELETE leave ----------
@router.delete("/{leave_id}")
async def delete_leave(leave_id: int, current_user: dict = Depends(get_current_user)):
    try:
        existing_resp = supabase.table("leaves").select("id").eq("id", leave_id).execute()
        if not get_supabase_data(existing_resp):
            raise HTTPException(status_code=404, detail=f"Leave with ID {leave_id} not found")
        supabase.table("leaves").delete().eq("id", leave_id).execute()
        return {"success": True, "detail": f"Leave {leave_id} deleted"}
    except Exception as e:
        logger.error(f"Error deleting leave {leave_id}: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error deleting leave: {str(e)}")