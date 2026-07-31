from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional
from app.supabase_client import supabase
from app.auth import require_role, get_current_user
from app.db_helpers import get_or_404
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter()

class OvertimeCreate(BaseModel):
    employee_name: str = Field(..., min_length=1)
    employee_id: str = Field(..., min_length=1)
    position: str = Field(..., min_length=1)
    overtime_type: str
    date: str
    # Reason/contact/exact times are no longer mandatory — a fast path for when someone is
    # pressed for time: either give start_time+end_time (hours computed from them) OR just
    # `hours` directly. validate_has_duration below requires one or the other.
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    hours: Optional[float] = Field(None, gt=0, le=24)
    reason: Optional[str] = None
    contact_number: Optional[str] = None
    emergency_contact: Optional[str] = None

    @validator('hours', always=True)
    def validate_has_duration(cls, v, values):
        if v is None and not (values.get('start_time') and values.get('end_time')):
            raise ValueError('Provide either start_time and end_time, or hours directly.')
        return v

class OvertimeUpdate(BaseModel):
    employee_name: Optional[str] = None
    employee_id: Optional[str] = None
    position: Optional[str] = None
    overtime_type: Optional[str] = None
    date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    hours: Optional[float] = Field(None, gt=0, le=24)
    reason: Optional[str] = None
    contact_number: Optional[str] = None
    emergency_contact: Optional[str] = None
    status: Optional[str] = None

# GET all overtime
@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def get_overtime(status: Optional[str] = None, overtime_type: Optional[str] = None):
    try:
        logger.info("Fetching overtime data...")
        
        query = supabase.table("overtime").select("*")
        
        if status:
            query = query.eq("status", status)
        if overtime_type:
            query = query.eq("overtime_type", overtime_type)
            
        response = query.order("created_at", desc=True).execute()
        
        logger.info(f"Supabase response: {response}")
        
        if hasattr(response, 'data'):
            data = response.data
        else:
            data = response
            
        logger.info(f"Returning {len(data) if data else 0} records")
        return data or []
        
    except Exception as e:
        logger.error(f"Error fetching overtime: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching overtime: {str(e)}")

# POST create overtime
@router.post("")
@router.post("/")
async def create_overtime(overtime: OvertimeCreate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Creating overtime for: {overtime.employee_name}")
        
        data_to_insert = {
            "employee_name": overtime.employee_name,
            "employee_id": overtime.employee_id,
            "position": overtime.position,
            "overtime_type": overtime.overtime_type,
            "date": overtime.date,
            "start_time": overtime.start_time,
            "end_time": overtime.end_time,
            "hours": overtime.hours,
            "reason": overtime.reason,
            "contact_number": overtime.contact_number,
            "emergency_contact": overtime.emergency_contact,
            "status": "pending",
            "applied_date": datetime.utcnow().isoformat()
        }
        
        logger.info(f"Inserting data: {data_to_insert}")
        
        response = supabase.table("overtime").insert(data_to_insert).execute()
        
        logger.info(f"Supabase insert response: {response}")
        
        if hasattr(response, 'data') and response.data:
            created_data = response.data[0]
            logger.info(f"Successfully created overtime with ID: {created_data.get('id')}")
            return created_data
        else:
            logger.error("No data returned from Supabase")
            raise HTTPException(status_code=500, detail="Failed to create overtime - no data returned")
            
    except Exception as e:
        logger.error(f"Error creating overtime: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating overtime: {str(e)}")

# PATCH update overtime
@router.patch("/{overtime_id}")
async def update_overtime(overtime_id: int, updated: OvertimeUpdate, authorization: Optional[str] = Header(None), current_user: dict = Depends(get_current_user)):
    # Any edit requires a signed-in user (current_user); approve/reject additionally
    # requires manager+ (checked below against the same Authorization header).
    if updated.status in ('approved', 'rejected'):
        approver = await require_role('manager')(authorization)
        logger.info(f"Approval action '{updated.status}' by {approver['email']} (role: {approver['role']})")
    try:
        logger.info(f"Updating overtime {overtime_id}")
        
        # Check if exists
        get_or_404(supabase, "overtime", overtime_id, detail="Overtime not found")
        
        # exclude_unset, not a None-filter: an explicitly-sent null must clear the
        # field, not be silently dropped. See work_orders (backend edec24a).
        data_to_update = updated.model_dump(exclude_unset=True)
        
        response = supabase.table("overtime").update(data_to_update).eq("id", overtime_id).execute()
        
        if hasattr(response, 'data') and response.data:
            return response.data[0]
        else:
            raise HTTPException(status_code=500, detail="Update failed")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating overtime: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating overtime: {str(e)}")

# DELETE overtime
@router.delete("/{overtime_id}")
async def delete_overtime(overtime_id: int, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Deleting overtime {overtime_id}")
        
        # Check if exists
        get_or_404(supabase, "overtime", overtime_id, detail="Overtime not found")
        
        supabase.table("overtime").delete().eq("id", overtime_id).execute()
        
        return {"success": True, "message": "Overtime deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting overtime: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting overtime: {str(e)}")
