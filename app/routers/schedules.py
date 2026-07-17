# app/routers/schedules.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import date, datetime
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedules", tags=["Maintenance Schedules"])

# ---------- Pydantic Models ----------
class AssignedPerson(BaseModel):
    type: str  # 'employee' or 'contractor'
    id: Optional[int] = None  # present if type == 'employee'
    name: str

class ScheduleBase(BaseModel):
    equipment_id: int
    title: str
    type: str  # 'maintenance', 'compliance', 'inspection'
    scheduled_date: date
    assigned_persons: List[AssignedPerson] = []
    notes: Optional[str] = None
    status: str = "planned"

class ScheduleCreate(ScheduleBase):
    pass

class ScheduleUpdate(BaseModel):
    equipment_id: Optional[int] = None
    title: Optional[str] = None
    type: Optional[str] = None
    scheduled_date: Optional[date] = None
    assigned_persons: Optional[List[AssignedPerson]] = None
    notes: Optional[str] = None
    status: Optional[str] = None

class ScheduleResponse(ScheduleBase):
    id: int
    equipment_name: str  # joined from equipment table
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

# ---------- Helper Functions ----------
def get_supabase_data(response):
    if hasattr(response, 'data'):
        return response.data
    return response

def fetch_equipment_name(equipment_id: int) -> str:
    """Get equipment name by id; raises 404 if not found."""
    resp = supabase.table("equipment").select("name").eq("id", equipment_id).execute()
    data = get_supabase_data(resp)
    if not data:
        raise HTTPException(status_code=404, detail=f"Equipment with id {equipment_id} not found")
    return data[0]["name"]

# ---------- Endpoints ----------
@router.get("", response_model=List[ScheduleResponse])
async def get_schedules():
    """Get all maintenance schedules, enriched with equipment name."""
    try:
        resp = supabase.table("maintenance_schedules").select("*").order("scheduled_date", desc=True).execute()
        schedules = get_supabase_data(resp) or []

        result = []
        for s in schedules:
            try:
                equipment_name = fetch_equipment_name(s["equipment_id"])
            except HTTPException:
                equipment_name = "Unknown"
            result.append({**s, "equipment_name": equipment_name})
        return result
    except Exception as e:
        logger.error(f"Error fetching schedules: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(schedule_id: int):
    """Get a single schedule by ID."""
    try:
        resp = supabase.table("maintenance_schedules").select("*").eq("id", schedule_id).execute()
        data = get_supabase_data(resp)
        if not data:
            raise HTTPException(status_code=404, detail="Schedule not found")
        schedule = data[0]
        equipment_name = fetch_equipment_name(schedule["equipment_id"])
        return {**schedule, "equipment_name": equipment_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching schedule {schedule_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("", response_model=ScheduleResponse)
async def create_schedule(schedule: ScheduleCreate, current_user: dict = Depends(get_current_user)):
    """Create a new maintenance schedule."""
    try:
        # Verify equipment exists (will raise 404 if not)
        equipment_name = fetch_equipment_name(schedule.equipment_id)

        # Convert to dict and prepare JSON fields
        data = schedule.dict()
        data["scheduled_date"] = schedule.scheduled_date.isoformat()
        data["assigned_persons"] = [p.dict() for p in schedule.assigned_persons]

        insert_resp = supabase.table("maintenance_schedules").insert(data).execute()
        created = get_supabase_data(insert_resp)
        if not created:
            raise HTTPException(status_code=500, detail="Failed to create schedule")
        new_schedule = created[0]

        return {**new_schedule, "equipment_name": equipment_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating schedule: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(schedule_id: int, updates: ScheduleUpdate, current_user: dict = Depends(get_current_user)):
    """Update an existing schedule."""
    try:
        existing_resp = supabase.table("maintenance_schedules").select("*").eq("id", schedule_id).execute()
        existing = get_supabase_data(existing_resp)
        if not existing:
            raise HTTPException(status_code=404, detail="Schedule not found")

        # Build update dict with only provided fields
        update_data = updates.dict(exclude_unset=True)

        # Determine equipment_id (existing or updated)
        new_equipment_id = update_data.get("equipment_id", existing[0]["equipment_id"])
        equipment_name = fetch_equipment_name(new_equipment_id)

        # Convert date to ISO string if present
        if "scheduled_date" in update_data and isinstance(update_data["scheduled_date"], date):
            update_data["scheduled_date"] = update_data["scheduled_date"].isoformat()

        # Convert assigned_persons to list of dicts if present
        if "assigned_persons" in update_data:
            update_data["assigned_persons"] = [p.dict() for p in update_data["assigned_persons"]]

        # Perform update
        update_resp = supabase.table("maintenance_schedules").update(update_data).eq("id", schedule_id).execute()
        updated = get_supabase_data(update_resp)
        if not updated:
            # If no data returned, fetch again
            fetch_resp = supabase.table("maintenance_schedules").select("*").eq("id", schedule_id).execute()
            fetched = get_supabase_data(fetch_resp)
            if not fetched:
                raise HTTPException(status_code=500, detail="No data returned after update")
            updated_schedule = fetched[0]
        else:
            updated_schedule = updated[0]

        return {**updated_schedule, "equipment_name": equipment_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating schedule {schedule_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: int, current_user: dict = Depends(require_role('manager'))):
    """Delete a schedule."""
    try:
        existing_resp = supabase.table("maintenance_schedules").select("id").eq("id", schedule_id).execute()
        if not get_supabase_data(existing_resp):
            raise HTTPException(status_code=404, detail="Schedule not found")
        supabase.table("maintenance_schedules").delete().eq("id", schedule_id).execute()
        return {"message": "Schedule deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting schedule {schedule_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")