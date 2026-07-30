from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.aggregation import count_by
import logging
import json

logger = logging.getLogger(__name__)
router = APIRouter()

# Pydantic Models matching frontend exactly
class TimesheetEntryCreate(BaseModel):
    employee_id: int
    date: date
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    regular_hours: float = 0
    overtime_hours: float = 0
    holiday_overtime_hours: float = 0
    nightshift_hours: float = 0
    standby_allowance: bool = False
    total_hours: float = 0
    status: str = "work"
    notes: Optional[str] = None
    overtime_periods: Optional[List[Dict[str, Any]]] = []
    callout_overtime_hours: float = 0
    callout_count: int = 0

class TimesheetEntryUpdate(BaseModel):
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    regular_hours: Optional[float] = None
    overtime_hours: Optional[float] = None
    holiday_overtime_hours: Optional[float] = None
    nightshift_hours: Optional[float] = None
    standby_allowance: Optional[bool] = None
    total_hours: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    overtime_periods: Optional[List[Dict[str, Any]]] = None
    callout_overtime_hours: Optional[float] = None
    callout_count: Optional[int] = None

# Timesheet Endpoints
@router.get("", dependencies=[Depends(get_current_user)])
async def get_timesheets(
    employee_id: Optional[int] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None)
):
    """Get timesheet entries with optional filters"""
    try:
        query = supabase.table("timesheets").select("*")
        
        if employee_id:
            query = query.eq("employee_id", employee_id)
        if start_date:
            query = query.gte("date", start_date.isoformat())
        if end_date:
            query = query.lte("date", end_date.isoformat())
            
        response = query.order("date", desc=True).execute()
        return response.data or []
        
    except Exception as e:
        logger.error(f"Error fetching timesheets: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching timesheets: {str(e)}")

@router.post("")
async def create_timesheet_entry(entry: TimesheetEntryCreate, current_user: dict = Depends(get_current_user)):
    """Create a new timesheet entry"""
    try:
        logger.info(f"Creating timesheet entry for employee {entry.employee_id} on {entry.date}")
        
        # Convert to dict
        data_to_insert = entry.dict()
        data_to_insert['date'] = data_to_insert['date'].isoformat()
        
        # Handle JSON fields
        if data_to_insert.get('overtime_periods'):
            data_to_insert['overtime_periods'] = json.dumps(data_to_insert['overtime_periods'])
        else:
            data_to_insert['overtime_periods'] = json.dumps([])
        
        # Add timestamps
        now = datetime.utcnow().isoformat()
        data_to_insert["created_at"] = now
        data_to_insert["updated_at"] = now
        
        # Check for existing entry
        existing = supabase.table("timesheets").select("*").eq(
            "employee_id", entry.employee_id
        ).eq("date", data_to_insert["date"]).execute()
        
        if existing.data:
            # Update existing
            entry_id = existing.data[0]["id"]
            response = supabase.table("timesheets").update(data_to_insert).eq("id", entry_id).execute()
            action = "updated"
            logger.info(f"Updated existing timesheet entry ID: {entry_id}")
        else:
            # Create new
            response = supabase.table("timesheets").insert(data_to_insert).execute()
            action = "created"
            logger.info(f"Created new timesheet entry")
        
        if response.data:
            result = response.data[0]
            # Parse JSON fields back
            if result.get('overtime_periods'):
                try:
                    result['overtime_periods'] = json.loads(result['overtime_periods'])
                except:
                    result['overtime_periods'] = []
            return {"action": action, "data": result}
        else:
            raise HTTPException(status_code=500, detail="Database operation failed")
            
    except Exception as e:
        logger.error(f"Error creating timesheet entry: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{entry_id}", dependencies=[Depends(get_current_user)])
async def get_timesheet_entry(entry_id: int):
    """Get a specific timesheet entry by ID"""
    try:
        response = supabase.table("timesheets").select("*").eq("id", entry_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Timesheet entry not found")
        
        result = response.data[0]
        # Parse JSON fields
        if result.get('overtime_periods'):
            try:
                result['overtime_periods'] = json.loads(result['overtime_periods'])
            except:
                result['overtime_periods'] = []
        
        return result
        
    except Exception as e:
        logger.error(f"Error fetching timesheet entry: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{entry_id}")
async def update_timesheet_entry(entry_id: int, updated: TimesheetEntryUpdate, current_user: dict = Depends(get_current_user)):
    """Update a timesheet entry"""
    try:
        # Check if entry exists
        existing = supabase.table("timesheets").select("*").eq("id", entry_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Timesheet entry not found")
        
        # exclude_unset, not a None-filter: an explicitly-sent null must clear the
        # field, not be silently dropped. See work_orders (backend edec24a).
        data_to_update = updated.model_dump(exclude_unset=True)
        
        # Handle JSON fields
        if 'overtime_periods' in data_to_update:
            data_to_update['overtime_periods'] = json.dumps(data_to_update['overtime_periods'])
        
        data_to_update["updated_at"] = datetime.utcnow().isoformat()
        
        response = supabase.table("timesheets").update(data_to_update).eq("id", entry_id).execute()
        
        if response.data:
            result = response.data[0]
            # Parse JSON fields
            if result.get('overtime_periods'):
                try:
                    result['overtime_periods'] = json.loads(result['overtime_periods'])
                except:
                    result['overtime_periods'] = []
            return result
        else:
            raise HTTPException(status_code=500, detail="Update failed")
            
    except Exception as e:
        logger.error(f"Error updating timesheet entry: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{entry_id}")
async def delete_timesheet_entry(entry_id: int, current_user: dict = Depends(require_role('manager'))):
    """Delete a timesheet entry"""
    try:
        # Check if entry exists
        existing = supabase.table("timesheets").select("*").eq("id", entry_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Timesheet entry not found")
        
        supabase.table("timesheets").delete().eq("id", entry_id).execute()
        
        logger.info(f"Deleted timesheet entry ID: {entry_id}")
        return {"success": True, "message": "Timesheet entry deleted successfully"}
        
    except Exception as e:
        logger.error(f"Error deleting timesheet entry: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Statistics endpoint
@router.get("/stats/summary")  # deliberately open — aggregate counts, not individual records
async def get_timesheet_stats(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None)
):
    """Get summary statistics for timesheets"""
    try:
        query = supabase.table("timesheets").select("*")
        
        if start_date:
            query = query.gte("date", start_date.isoformat())
        if end_date:
            query = query.lte("date", end_date.isoformat())
            
        response = query.execute()
        records = response.data or []
        
        # Simple calculations
        total_entries = len(records)
        total_regular = sum(r.get('regular_hours', 0) for r in records)
        total_overtime = sum(r.get('overtime_hours', 0) for r in records)
        total_holiday = sum(r.get('holiday_overtime_hours', 0) for r in records)
        total_nightshift = sum(r.get('nightshift_hours', 0) for r in records)
        total_hours = sum(r.get('total_hours', 0) for r in records)
        
        # Count by status
        status_counts = count_by(records, 'status')
        
        # Get unique employees
        employee_ids = set(record['employee_id'] for record in records if 'employee_id' in record)
        
        # Get days with entries
        unique_dates = set(record['date'] for record in records if 'date' in record)
        
        # Count standby days
        standby_days = sum(1 for record in records if record.get('standby_allowance', False))
        
        return {
            "total_entries": total_entries,
            "total_employees": len(employee_ids),
            "total_days": len(unique_dates),
            "total_hours": {
                "regular": total_regular,
                "overtime": total_overtime,
                "holiday_overtime": total_holiday,
                "nightshift": total_nightshift,
                "total": total_hours
            },
            "status_breakdown": status_counts,
            "standby_days": standby_days,
            "average_hours_per_day": total_hours / len(unique_dates) if unique_dates else 0,
            "average_hours_per_employee": total_hours / len(employee_ids) if employee_ids else 0
        }
        
    except Exception as e:
        logger.error(f"Error fetching timesheet stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))