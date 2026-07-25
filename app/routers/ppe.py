from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, date
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging
import json

logger = logging.getLogger(__name__)
router = APIRouter()

# Custom JSON encoder to handle date objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)

# Pydantic Models
class PPEIssueCreate(BaseModel):
    employee_name: str = Field(..., min_length=1)
    employee_id: str = Field(..., min_length=1)
    department: str = Field(..., min_length=1)
    position: str = Field(..., min_length=1)
    ppe_type: str = Field(..., min_length=1)
    item_name: str = Field(..., min_length=1)
    size: Optional[str] = None
    issue_date: date
    expiry_date: Optional[date] = None
    condition: str = Field(default="good")
    status: str = Field(default="active")
    notes: Optional[str] = None
    issued_by: Optional[str] = None
    location: Optional[str] = None
    mine_section: Optional[str] = None

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

class PPEIssueUpdate(BaseModel):
    employee_name: Optional[str] = None
    employee_id: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    ppe_type: Optional[str] = None
    item_name: Optional[str] = None
    size: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    condition: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    issued_by: Optional[str] = None
    location: Optional[str] = None
    mine_section: Optional[str] = None

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

# Helper function to convert dates in records
def convert_dates_to_iso(record):
    """Convert date objects to ISO format strings for JSON serialization"""
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(value, (date, datetime)):
                record[key] = value.isoformat()
    return record

# PPE Records Endpoints
@router.get("", dependencies=[Depends(get_current_user)])
async def get_ppe_records(
    status: Optional[str] = None,
    ppe_type: Optional[str] = None,
    department: Optional[str] = None,
    location: Optional[str] = None,
    employee_id: Optional[str] = None
):
    try:
        query = supabase.table("ppe_records").select("*")
        
        if status and status != 'all':
            query = query.eq("status", status)
        if ppe_type and ppe_type != 'all':
            query = query.eq("ppe_type", ppe_type)
        if department and department != 'all':
            query = query.eq("department", department)
        if location and location != 'all':
            query = query.eq("location", location)
        if employee_id and employee_id != 'all':
            query = query.eq("employee_id", employee_id)
            
        response = query.order("created_at", desc=True).execute()
        
        # Convert dates to ISO format for JSON serialization
        records = response.data or []
        for record in records:
            convert_dates_to_iso(record)
            
        return records
        
    except Exception as e:
        logger.error(f"Error fetching PPE records: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching PPE records: {str(e)}")

@router.post("")
async def create_ppe_record(record: PPEIssueCreate, current_user: dict = Depends(get_current_user)):
    try:
        # Convert Pydantic model to dict and handle date serialization
        data_to_insert = record.dict()
        
        # Ensure dates are properly formatted for database
        if data_to_insert.get('issue_date'):
            data_to_insert['issue_date'] = data_to_insert['issue_date'].isoformat()
        if data_to_insert.get('expiry_date'):
            data_to_insert['expiry_date'] = data_to_insert['expiry_date'].isoformat()
            
        data_to_insert["created_at"] = datetime.utcnow().isoformat()
        
        response = supabase.table("ppe_records").insert(data_to_insert).execute()
        
        if response.data:
            # Convert dates to ISO format for JSON response
            result = response.data[0]
            convert_dates_to_iso(result)
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to create PPE record")
            
    except Exception as e:
        logger.error(f"Error creating PPE record: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating PPE record: {str(e)}")

@router.get("/{record_id:int}", dependencies=[Depends(get_current_user)])
async def get_ppe_record(record_id: int):
    try:
        response = supabase.table("ppe_records").select("*").eq("id", record_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="PPE record not found")
        
        result = response.data[0]
        convert_dates_to_iso(result)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching PPE record: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching PPE record: {str(e)}")

@router.patch("/{record_id}")
async def update_ppe_record(record_id: int, updated: PPEIssueUpdate, current_user: dict = Depends(get_current_user)):
    try:
        existing = supabase.table("ppe_records").select("*").eq("id", record_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="PPE record not found")
        
        # exclude_unset, not a None-filter: an explicitly-sent null must clear the
        # field, not be silently dropped. See work_orders (backend edec24a).
        data_to_update = updated.model_dump(exclude_unset=True)
        
        # Convert dates to ISO format for database
        if data_to_update.get('issue_date'):
            data_to_update['issue_date'] = data_to_update['issue_date'].isoformat()
        if data_to_update.get('expiry_date'):
            data_to_update['expiry_date'] = data_to_update['expiry_date'].isoformat()
            
        data_to_update["updated_at"] = datetime.utcnow().isoformat()
        
        response = supabase.table("ppe_records").update(data_to_update).eq("id", record_id).execute()
        
        if response.data:
            result = response.data[0]
            convert_dates_to_iso(result)
            return result
        else:
            raise HTTPException(status_code=500, detail="Update failed")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating PPE record: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating PPE record: {str(e)}")

@router.delete("/{record_id}")
async def delete_ppe_record(record_id: int, current_user: dict = Depends(require_role('manager'))):
    try:
        existing = supabase.table("ppe_records").select("*").eq("id", record_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="PPE record not found")
        
        supabase.table("ppe_records").delete().eq("id", record_id).execute()
        return {"success": True, "message": "PPE record deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting PPE record: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting PPE record: {str(e)}")

@router.get("/employee/{employee_id}", dependencies=[Depends(get_current_user)])
async def get_employee_ppe_records(employee_id: str):
    try:
        response = supabase.table("ppe_records").select("*").eq("employee_id", employee_id).order("issue_date", desc=True).execute()
        
        # Convert dates to ISO format for JSON serialization
        records = response.data or []
        for record in records:
            convert_dates_to_iso(record)
            
        return records
        
    except Exception as e:
        logger.error(f"Error fetching employee PPE records: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching employee PPE records: {str(e)}")

# Statistics Endpoint
@router.get("/stats/summary", dependencies=[Depends(get_current_user)])
async def get_ppe_stats():
    try:
        # Get total records count
        records_response = supabase.table("ppe_records").select("id", count="exact").execute()
        total_records = len(records_response.data) if records_response.data else 0
        
        # Get records by status
        status_response = supabase.table("ppe_records").select("status").execute()
        status_counts = {}
        if status_response.data:
            for record in status_response.data:
                status = record.get('status', 'unknown')
                status_counts[status] = status_counts.get(status, 0) + 1
        
        # Get records by condition
        condition_response = supabase.table("ppe_records").select("condition").execute()
        condition_counts = {}
        if condition_response.data:
            for record in condition_response.data:
                condition = record.get('condition', 'unknown')
                condition_counts[condition] = condition_counts.get(condition, 0) + 1
        
        # Count expiring soon (within 30 days) and expired
        today = date.today()
        records_all = supabase.table("ppe_records").select("expiry_date, status").execute()
        expiring_soon = 0
        expired = 0
        
        if records_all.data:
            for record in records_all.data:
                expiry_date_str = record.get('expiry_date')
                status = record.get('status', 'active')
                
                if expiry_date_str and status == 'active':
                    try:
                        expiry = datetime.strptime(expiry_date_str, '%Y-%m-%d').date()
                        days_until_expiry = (expiry - today).days
                        
                        if days_until_expiry < 0:
                            expired += 1
                        elif days_until_expiry <= 30:
                            expiring_soon += 1
                    except (ValueError, TypeError):
                        # Handle invalid date formats
                        continue
        
        # Get unique employees count
        employees_response = supabase.table("ppe_records").select("employee_id").execute()
        unique_employees = len(set(record['employee_id'] for record in employees_response.data)) if employees_response.data else 0
        
        return {
            "total_records": total_records,
            "unique_employees": unique_employees,
            "status_breakdown": status_counts,
            "condition_breakdown": condition_counts,
            "expiring_soon": expiring_soon,
            "expired": expired
        }
        
    except Exception as e:
        logger.error(f"Error fetching PPE stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching PPE stats: {str(e)}")


# ─── PPE replacement matrix ────────────────────────────────────────────────────
# Default months-until-expiry per item type (calculated from the issue date). Overrides
# are stored in the ppe_matrix table (see supabase_migration_ppe_matrix.sql) and merged
# over these; the endpoints degrade gracefully to defaults if that table doesn't exist.

import calendar

# interval 0 = no expiry (item doesn't expire / isn't replaced on a schedule).
PPE_MATRIX_DEFAULTS = {
    "worksuit": 6, "gumboots": 6, "safety_shoes": 6,
    "helmet": 24, "Cap_lamp_belt": 24, "pneumo_jacket": 24, "harness": 24,
    "vest": 3, "glasses": 3, "respirator": 1, "rainsuit": 6,
    "gloves": 0, "overall": 6,
}


def _add_months(iso_date, months):
    """issue date (YYYY-MM-DD) + N months → expiry date string, clamping day overflow."""
    if not iso_date or not months:
        return None
    try:
        d = date.fromisoformat(str(iso_date)[:10])
    except Exception:
        return None
    m = d.month - 1 + int(months)
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day).isoformat()


def _matrix_overrides():
    """{ppe_type: interval_months} from the ppe_matrix table, or {} if unavailable."""
    try:
        resp = supabase.table("ppe_matrix").select("ppe_type, interval_months").execute()
        return {r["ppe_type"]: r["interval_months"] for r in (resp.data or [])}
    except Exception as e:
        logger.info(f"ppe_matrix table unavailable, using defaults: {e}")
        return {}


class MatrixEntry(BaseModel):
    ppe_type: str = Field(..., min_length=1)
    interval_months: int = Field(..., ge=0, le=120)   # 0 = no expiry


@router.get("/matrix", dependencies=[Depends(get_current_user)])
async def get_ppe_matrix():
    """Effective matrix: company defaults with any saved overrides applied."""
    matrix = dict(PPE_MATRIX_DEFAULTS)
    matrix.update(_matrix_overrides())
    return matrix


def _apply_matrix_to_type(ppe_type: str, interval: int) -> int:
    """Recalculate expiry = issue_date + interval for every ACTIVE record of this type,
    overwriting whatever expiry_date is currently stored (including a manually-typed
    one — the matrix is the source of truth once a type's interval is set/changed).
    Returns the number of records updated."""
    recs = supabase.table("ppe_records").select("id, issue_date").eq("ppe_type", ppe_type).eq("status", "active").execute()
    updated = 0
    for r in (recs.data or []):
        # interval 0 = no expiry → clear the date; otherwise recompute from issue date.
        new_exp = None if interval == 0 else _add_months(r.get("issue_date"), interval)
        if interval == 0 or new_exp:
            supabase.table("ppe_records").update({
                "expiry_date": new_exp,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", r["id"]).execute()
            updated += 1
    return updated


@router.put("/matrix")
async def set_ppe_matrix_entry(entry: MatrixEntry, current_user: dict = Depends(require_role('manager'))):
    """Set (upsert) the interval for one PPE type, then immediately recalculate expiry
    for every active record of that type so the matrix and stored data never drift
    apart — no separate 'apply' step needed."""
    try:
        supabase.table("ppe_matrix").upsert({
            "ppe_type": entry.ppe_type,
            "interval_months": entry.interval_months,
            "updated_at": datetime.utcnow().isoformat(),
        }, on_conflict="ppe_type").execute()
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"Could not save matrix — has the ppe_matrix table been created? {e}")
    try:
        updated = _apply_matrix_to_type(entry.ppe_type, entry.interval_months)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Matrix saved, but recalculating existing records failed: {e}")
    return {"ppe_type": entry.ppe_type, "interval_months": entry.interval_months, "updated": updated}


@router.post("/matrix/{ppe_type}/apply")
async def apply_ppe_matrix(ppe_type: str, current_user: dict = Depends(require_role('manager'))):
    """Recalculate expiry for every ACTIVE record of this type against the current
    matrix interval — the manual per-type control (PUT /matrix now does this
    automatically on save, but this stays for re-running it standalone, e.g. after a
    record's issue_date was edited)."""
    overrides = _matrix_overrides()
    if ppe_type in overrides:
        interval = overrides[ppe_type]
    elif ppe_type in PPE_MATRIX_DEFAULTS:
        interval = PPE_MATRIX_DEFAULTS[ppe_type]
    else:
        raise HTTPException(status_code=400, detail=f"No interval configured for '{ppe_type}'")
    try:
        updated = _apply_matrix_to_type(ppe_type, interval)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not recalculate records: {e}")
    return {"ppe_type": ppe_type, "interval_months": interval, "updated": updated}


@router.post("/matrix/apply-all")
async def apply_ppe_matrix_all(current_user: dict = Depends(require_role('manager'))):
    """Recalculate expiry for every ACTIVE record of every PPE type against the
    current matrix — a one-shot fix for records left stale by a past interval change,
    without clicking 'Recalculate' once per type."""
    matrix = dict(PPE_MATRIX_DEFAULTS)
    matrix.update(_matrix_overrides())
    results = {}
    for ppe_type, interval in matrix.items():
        try:
            results[ppe_type] = _apply_matrix_to_type(ppe_type, interval)
        except Exception as e:
            logger.error(f"Failed recalculating '{ppe_type}': {e}")
            results[ppe_type] = None
    return {"results": results, "total_updated": sum(v for v in results.values() if v)}