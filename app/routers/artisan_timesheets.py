# artisan_timesheets.py — Formal monthly artisan daily timesheet documents.
# Supabase table: artisan_timesheets (see supabase_migration_artisan_timesheets.sql)

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

from app.supabase_client import supabase, rows, one_row
from app.auth import get_current_user
from app.db_helpers import get_or_404
from app.serialization import encode_json_fields, decode_json_fields

logger = logging.getLogger(__name__)

router = APIRouter()

TABLE = "artisan_timesheets"
JSON_FIELDS = ["daily_rows"]


class DailyRow(BaseModel):
    date: str
    day: str
    day_status: str = ""
    normal_hrs: float = 0
    ot_15: float = 0
    ot_20: float = 0
    sb_15: float = 0
    sb_20: float = 0
    night_shift: float = 0
    on_standby: bool = False
    sign_in_time: str = ""
    sign_in_signature: str = ""
    sign_out_time: str = ""
    sign_out_signature: str = ""
    comments: str = ""

    class Config:
        extra = "allow"


class ArtisanTimesheetCreate(BaseModel):
    employee_id: str = Field(..., min_length=1)
    employee_db_id: Optional[int] = None
    employee_name: str = Field(..., min_length=1)
    id_number: Optional[str] = None
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)
    shift_rate: Optional[float] = None
    hourly_rate: Optional[float] = None
    daily_rows: List[DailyRow] = Field(default_factory=list)
    compiled_by: Optional[str] = None
    compiled_by_signature: Optional[str] = None
    approved_electrical_foreman: Optional[str] = None
    approved_electrical_foreman_signature: Optional[str] = None
    approved_mechanical_foreman: Optional[str] = None
    approved_mechanical_foreman_signature: Optional[str] = None
    authorized_by: Optional[str] = None
    authorized_by_signature: Optional[str] = None


class ArtisanTimesheetUpdate(BaseModel):
    employee_id: Optional[str] = None
    employee_db_id: Optional[int] = None
    employee_name: Optional[str] = None
    id_number: Optional[str] = None
    year: Optional[int] = Field(None, ge=2000, le=2100)
    month: Optional[int] = Field(None, ge=1, le=12)
    shift_rate: Optional[float] = None
    hourly_rate: Optional[float] = None
    daily_rows: Optional[List[DailyRow]] = None
    compiled_by: Optional[str] = None
    compiled_by_signature: Optional[str] = None
    approved_electrical_foreman: Optional[str] = None
    approved_electrical_foreman_signature: Optional[str] = None
    approved_mechanical_foreman: Optional[str] = None
    approved_mechanical_foreman_signature: Optional[str] = None
    authorized_by: Optional[str] = None
    authorized_by_signature: Optional[str] = None


def _decode(row: Dict[str, Any]) -> Dict[str, Any]:
    return decode_json_fields(row, JSON_FIELDS)


def _now() -> str:
    return datetime.utcnow().isoformat()


@router.get("")
async def list_artisan_timesheets(
    employee_id: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    try:
        query = supabase.table(TABLE).select("*")
        if employee_id:
            query = query.eq("employee_id", employee_id)
        if year is not None:
            query = query.eq("year", year)
        if month is not None:
            query = query.eq("month", month)
        response = query.order("year", desc=True).order("month", desc=True).order("employee_name").execute()
        return [_decode(r) for r in rows(response)]
    except Exception as e:
        logger.error(f"Error listing artisan timesheets: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing artisan timesheets: {e}")


@router.get("/{timesheet_id}")
async def get_artisan_timesheet(timesheet_id: int, current_user: dict = Depends(get_current_user)):
    try:
        row = get_or_404(supabase, TABLE, timesheet_id, detail="Artisan timesheet not found")
        return _decode(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching artisan timesheet {timesheet_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching artisan timesheet: {e}")


@router.post("")
async def create_artisan_timesheet(body: ArtisanTimesheetCreate, current_user: dict = Depends(get_current_user)):
    try:
        existing = (
            supabase.table(TABLE)
            .select("id")
            .eq("employee_id", body.employee_id)
            .eq("year", body.year)
            .eq("month", body.month)
            .execute()
        )
        if one_row(existing):
            raise HTTPException(
                status_code=409,
                detail="A timesheet for this employee, month, and year already exists. Open and update it instead.",
            )

        payload = encode_json_fields(body.dict(), JSON_FIELDS)
        now = _now()
        payload["created_at"] = now
        payload["updated_at"] = now

        response = supabase.table(TABLE).insert(payload).execute()
        created = one_row(response)
        if not created:
            raise HTTPException(status_code=500, detail="Create failed")
        return _decode(created)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating artisan timesheet: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating artisan timesheet: {e}")


@router.patch("/{timesheet_id}")
async def update_artisan_timesheet(
    timesheet_id: int,
    body: ArtisanTimesheetUpdate,
    current_user: dict = Depends(get_current_user),
):
    try:
        get_or_404(supabase, TABLE, timesheet_id, detail="Artisan timesheet not found")
        data = body.dict(exclude_unset=True)
        if not data:
            row = get_or_404(supabase, TABLE, timesheet_id, detail="Artisan timesheet not found")
            return _decode(row)

        payload = encode_json_fields(data, JSON_FIELDS)
        payload["updated_at"] = _now()

        response = supabase.table(TABLE).update(payload).eq("id", timesheet_id).execute()
        updated = one_row(response)
        if not updated:
            raise HTTPException(status_code=500, detail="Update failed")
        return _decode(updated)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating artisan timesheet {timesheet_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating artisan timesheet: {e}")


@router.delete("/{timesheet_id}")
async def delete_artisan_timesheet(timesheet_id: int, current_user: dict = Depends(get_current_user)):
    try:
        get_or_404(supabase, TABLE, timesheet_id, detail="Artisan timesheet not found")
        supabase.table(TABLE).delete().eq("id", timesheet_id).execute()
        return {"success": True, "message": "Artisan timesheet deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting artisan timesheet {timesheet_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting artisan timesheet: {e}")
