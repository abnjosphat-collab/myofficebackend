# artisan_timesheets.py — Formal monthly artisan daily timesheet documents.
# Supabase table: artisan_timesheets (see supabase_migration_artisan_timesheets.sql)

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Set
from datetime import date, datetime, timedelta
from calendar import monthrange
import logging

from app.supabase_client import supabase, rows, one_row
from app.auth import get_current_user
from app.db_helpers import fetch_all_pages, get_or_404
from app.serialization import encode_json_fields, decode_json_fields

logger = logging.getLogger(__name__)

router = APIRouter()

TABLE = "artisan_timesheets"
JSON_FIELDS = ["daily_rows"]
# What a list needs to show and open a timesheet: no daily rows (so no per-day signatures, which are most of a record's size).
SUMMARY_COLUMNS = (
    "id,employee_id,employee_db_id,employee_name,id_number,year,month,shift_rate,hourly_rate,"
    "compiled_by,approved_electrical_foreman,approved_mechanical_foreman,authorized_by,created_at,updated_at"
)

# Day statuses owned by the Leaves module — mirrors the frontend's
# LEAVE_DAY_STATUSES in app/artisan-timesheets/dayStatus.ts. Nobody works on
# leave: a day with one of these statuses (or covered by approved leave, even
# unmarked) credits 8 normal hours and nothing else — no overtime, no standby,
# no sign-in/out.
LEAVE_DAY_STATUSES = frozenset({"leave", "sick", "special_leave", "maternity", "study", "lieu"})
LEAVE_NORMAL_HRS = 8


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


def _approved_leave_dates(employee_id: str, year: int, month: int) -> Set[str]:
    """Dates in (year, month) covered by approved leave for this employee.

    Matched loosely (trimmed, case-insensitive) like the frontend's sameEmployee,
    and tolerant of datetime-suffixed dates like dayPart — a leave typed with a
    stray space still blocks work on those dates."""
    last_day = monthrange(year, month)[1]
    month_start = f"{year:04d}-{month:02d}-01"
    month_end = f"{year:04d}-{month:02d}-{last_day:02d}"
    response = (
        supabase.table("leaves")
        .select("employee_id,start_date,end_date")
        .eq("status", "approved")
        .gte("end_date", month_start)
        .lte("start_date", month_end)
        .execute()
    )
    want = (employee_id or "").strip().upper()
    dates: Set[str] = set()
    for leave in rows(response):
        if (leave.get("employee_id") or "").strip().upper() != want:
            continue
        start = str(leave.get("start_date") or "")[:10]
        end = str(leave.get("end_date") or "")[:10]
        if not start or not end or end < start:
            continue
        day = max(start, month_start)
        stop = min(end, month_end)
        while day <= stop:
            dates.add(day)
            day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    return dates


def _leave_violations(daily_rows: List[DailyRow], leave_dates: Set[str]) -> List[str]:
    """One message per work trace found on a leave day — by day status, or by
    approved leave covering the date even when the row was never marked."""
    found: List[str] = []
    for row in daily_rows or []:
        day = (row.date or "")[:10]
        status_leave = (row.day_status or "") in LEAVE_DAY_STATUSES
        if not status_leave and day not in leave_dates:
            continue
        label = row.day_status if status_leave else "approved leave"
        worked = []
        if row.ot_15:
            worked.append(f"{row.ot_15:g}h overtime at 1.5x")
        if row.ot_20:
            worked.append(f"{row.ot_20:g}h overtime at 2.0x")
        if row.sb_15:
            worked.append(f"{row.sb_15:g}h standby at 1.5x")
        if row.sb_20:
            worked.append(f"{row.sb_20:g}h standby at 2.0x")
        if row.night_shift:
            worked.append(f"{row.night_shift:g}h night shift")
        if worked:
            found.append(f"{day} is {label} but has {', '.join(worked)} recorded — nobody works on a leave day.")
        if row.normal_hrs != LEAVE_NORMAL_HRS:
            found.append(f"{day} is {label} but credits {row.normal_hrs:g} normal hours instead of 8.")
        if row.on_standby:
            found.append(f"{day} is {label} but is marked on standby — nobody works on a leave day.")
        if row.sign_in_time or row.sign_out_time or row.sign_in_signature or row.sign_out_signature:
            found.append(f"{day} is {label} but has a sign-in/out record — nobody works on a leave day.")
    return found


def _reject_work_on_leave(daily_rows: List[DailyRow], leave_dates: Set[str]) -> None:
    problems = _leave_violations(daily_rows, leave_dates)
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))


@router.get("")
async def list_artisan_timesheets(
    employee_id: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    summary: bool = Query(False, description="Return each timesheet without its daily rows and signatures; open one with GET /{id}."),
    current_user: dict = Depends(get_current_user),
):
    try:
        query = supabase.table(TABLE).select(SUMMARY_COLUMNS if summary else "*")
        if employee_id:
            query = query.eq("employee_id", employee_id)
        if year is not None:
            query = query.eq("year", year)
        if month is not None:
            query = query.eq("month", month)
        query = query.order("year", desc=True).order("month", desc=True).order("employee_name").order("id")
        if summary:
            # Small rows, so read every page rather than stopping at PostgREST's first 1,000.
            return fetch_all_pages(lambda start, end: query.range(start, end).execute(), extract_rows=rows)
        response = query.execute()
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

        _reject_work_on_leave(
            body.daily_rows,
            _approved_leave_dates(body.employee_id, body.year, body.month),
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
        existing = get_or_404(supabase, TABLE, timesheet_id, detail="Artisan timesheet not found")
        data = body.dict(exclude_unset=True)
        if not data:
            return _decode(existing)

        if body.daily_rows is not None:
            raw_employee = data.get("employee_id", existing.get("employee_id"))
            raw_year = data.get("year", existing.get("year"))
            raw_month = data.get("month", existing.get("month"))
            if not isinstance(raw_employee, str) or not isinstance(raw_year, int) or not isinstance(raw_month, int):
                raise HTTPException(status_code=500, detail="Timesheet is missing employee or period")
            leave_dates = _approved_leave_dates(
                raw_employee, raw_year, raw_month
            )
            _reject_work_on_leave(body.daily_rows, leave_dates)

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
