# standby.py — Shift Roster router
# Manages repeating shift cycles (10-4, 5-2, custom) and standby duties.
# Supabase table: standby_schedules
#
# ── SQL Migration — run once in Supabase SQL editor ──────────────────────────
#
#   Option A: Drop & recreate (clean slate, loses old data)
#   --------------------------------------------------------
#   DROP TABLE IF EXISTS standby_schedules;
#   CREATE TABLE standby_schedules (
#     id             bigserial    primary key,
#     employee_id    text         not null,
#     employee_name  text         not null,
#     designation    text,
#     department     text,
#     section        text,
#     phone          text,
#     shift_type     text         not null default '10-4',
#     on_days        integer      not null default 10,
#     off_days       integer      not null default 4,
#     cycle_start_date date       not null,
#     notes          text,
#     is_active      boolean      not null default true,
#     created_at     timestamptz  not null default now(),
#     updated_at     timestamptz  not null default now()
#   );
#
#   Option B: Add columns to existing table (keeps old rows)
#   --------------------------------------------------------
#   ALTER TABLE standby_schedules
#     ADD COLUMN IF NOT EXISTS employee_name     text         NOT NULL DEFAULT '',
#     ADD COLUMN IF NOT EXISTS designation       text,
#     ADD COLUMN IF NOT EXISTS section           text,
#     ADD COLUMN IF NOT EXISTS phone             text,
#     ADD COLUMN IF NOT EXISTS shift_type        text         NOT NULL DEFAULT '10-4',
#     ADD COLUMN IF NOT EXISTS on_days           integer      NOT NULL DEFAULT 10,
#     ADD COLUMN IF NOT EXISTS off_days          integer      NOT NULL DEFAULT 4,
#     ADD COLUMN IF NOT EXISTS cycle_start_date  date,
#     ADD COLUMN IF NOT EXISTS is_active         boolean      NOT NULL DEFAULT true,
#     ADD COLUMN IF NOT EXISTS standby_periods   jsonb        NOT NULL DEFAULT '[]',
#     ADD COLUMN IF NOT EXISTS updated_at        timestamptz;
#
# ─────────────────────────────────────────────────────────────────────────────

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import date, datetime
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.db_helpers import get_or_404
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/standby", tags=["Shift Roster"])

TABLE = "standby_schedules"
VALID_SHIFT_TYPES = {"10-4", "5-2", "standby", "custom"}

# ─── Models ───────────────────────────────────────────────────────────────────

class ShiftRosterCreate(BaseModel):
    employee_id:      str           = Field(...)
    employee_name:    str           = Field(...)
    designation:      Optional[str] = None
    department:       Optional[str] = None
    section:          Optional[str] = None
    phone:            Optional[str] = None
    shift_type:       str           = Field(default="10-4")
    on_days:          int           = Field(default=10, ge=0)
    off_days:         int           = Field(default=4,  ge=0)
    cycle_start_date: date          = Field(...)
    notes:            Optional[str]  = None
    is_active:        bool           = True
    standby_periods:       List[dict]    = Field(default_factory=list)
    shift_label:           Optional[str] = None
    shift_hours:           Optional[str] = None
    shift_timing_periods:  List[dict]    = Field(default_factory=list)
    day_overrides:         List[dict]    = Field(default_factory=list)

    @validator("shift_type")
    def validate_shift_type(cls, v: str) -> str:
        if v not in VALID_SHIFT_TYPES:
            raise ValueError(f"shift_type must be one of: {sorted(VALID_SHIFT_TYPES)}")
        return v


class ShiftRosterUpdate(BaseModel):
    employee_id:      Optional[str]  = None
    employee_name:    Optional[str]  = None
    designation:      Optional[str]  = None
    department:       Optional[str]  = None
    section:          Optional[str]  = None
    phone:            Optional[str]  = None
    shift_type:       Optional[str]  = None
    on_days:          Optional[int]  = None
    off_days:         Optional[int]  = None
    cycle_start_date: Optional[date] = None
    notes:            Optional[str]       = None
    is_active:        Optional[bool]      = None
    standby_periods:      Optional[List[dict]] = None
    shift_label:          Optional[str]       = None
    shift_hours:          Optional[str]       = None
    shift_timing_periods: Optional[List[dict]] = None
    day_overrides:        Optional[List[dict]] = None

    @validator("shift_type")
    def validate_shift_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_SHIFT_TYPES:
            raise ValueError(f"shift_type must be one of: {sorted(VALID_SHIFT_TYPES)}")
        return v


class ShiftRosterResponse(BaseModel):
    id:               int
    employee_id:      str
    employee_name:    str
    designation:      Optional[str]      = None
    department:       Optional[str]      = None
    section:          Optional[str]      = None
    phone:            Optional[str]      = None
    shift_type:       str
    on_days:          int
    off_days:         int
    cycle_start_date: date
    notes:            Optional[str]       = None
    is_active:        bool
    standby_periods:      Optional[List[dict]] = Field(default_factory=list)
    shift_label:          Optional[str]       = None
    shift_hours:          Optional[str]       = None
    shift_timing_periods: Optional[List[dict]] = Field(default_factory=list)
    day_overrides:        Optional[List[dict]] = Field(default_factory=list)
    created_at:           Optional[datetime]  = None

    class Config:
        from_attributes = True
        json_encoders = {
            date:     lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat(),
        }

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _rows(response) -> list:
    return response.data if hasattr(response, "data") else response or []


def _require_exists(assignment_id: int) -> None:
    resp = supabase.table(TABLE).select("id").eq("id", assignment_id).execute()
    if not _rows(resp):
        raise HTTPException(status_code=404, detail=f"Assignment {assignment_id} not found")

# ─── GET all ──────────────────────────────────────────────────────────────────

@router.get("",  response_model=List[ShiftRosterResponse], dependencies=[Depends(get_current_user)])
@router.get("/", response_model=List[ShiftRosterResponse], dependencies=[Depends(get_current_user)])
async def list_assignments(active_only: bool = False):
    try:
        q = supabase.table(TABLE).select("*")
        if active_only:
            q = q.eq("is_active", True)
        rows = _rows(q.order("created_at", desc=True).execute())
        return rows or []
    except Exception as e:
        logger.error(f"list_assignments error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─── GET one ──────────────────────────────────────────────────────────────────

@router.get("/{assignment_id}", response_model=ShiftRosterResponse, dependencies=[Depends(get_current_user)])
async def get_assignment(assignment_id: int):
    try:
        return get_or_404(supabase, TABLE, assignment_id, detail=f"Assignment {assignment_id} not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_assignment({assignment_id}) error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─── POST create ──────────────────────────────────────────────────────────────

@router.post("",  response_model=ShiftRosterResponse, status_code=201)
@router.post("/", response_model=ShiftRosterResponse, status_code=201)
async def create_assignment(payload: ShiftRosterCreate, current_user: dict = Depends(get_current_user)):
    try:
        data: dict = {
            "employee_id":      payload.employee_id,
            "employee_name":    payload.employee_name,
            "designation":      payload.designation,
            "department":       payload.department,
            "section":          payload.section,
            "phone":            payload.phone,
            "shift_type":       payload.shift_type,
            "on_days":          payload.on_days,
            "off_days":         payload.off_days,
            "cycle_start_date": payload.cycle_start_date.isoformat(),
            "notes":            payload.notes,
            "is_active":        payload.is_active,
        }
        # Only include columns that may not exist yet until migration is run
        if payload.standby_periods:
            data["standby_periods"] = payload.standby_periods
        if payload.shift_label is not None:
            data["shift_label"] = payload.shift_label
        if payload.shift_hours is not None:
            data["shift_hours"] = payload.shift_hours
        if payload.shift_timing_periods:
            data["shift_timing_periods"] = payload.shift_timing_periods
        if payload.day_overrides:
            data["day_overrides"] = payload.day_overrides
        rows = _rows(supabase.table(TABLE).insert(data).execute())
        if not rows:
            raise HTTPException(status_code=500, detail="No data returned after insert")
        return rows[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_assignment error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Columns added by the optional migration — may not exist yet in older deployments
_OPTIONAL_COLS = frozenset({"standby_periods", "shift_label", "shift_hours", "shift_timing_periods", "day_overrides"})

def _is_missing_column_error(e: Exception) -> bool:
    s = str(e)
    return "PGRST204" in s or ("Could not find" in s and "column" in s.lower())

# ─── PUT update ───────────────────────────────────────────────────────────────

@router.put("/{assignment_id}", response_model=ShiftRosterResponse)
async def update_assignment(assignment_id: int, payload: ShiftRosterUpdate, current_user: dict = Depends(get_current_user)):
    try:
        _require_exists(assignment_id)

        data = payload.dict(exclude_unset=True)
        if "cycle_start_date" in data and isinstance(data["cycle_start_date"], date):
            data["cycle_start_date"] = data["cycle_start_date"].isoformat()
        data["updated_at"] = datetime.utcnow().isoformat()

        try:
            rows = _rows(supabase.table(TABLE).update(data).eq("id", assignment_id).execute())
        except Exception as inner_e:
            if _is_missing_column_error(inner_e):
                # Migration not yet applied — strip optional cols and retry with core fields only
                missing_keys = [k for k in data if k in _OPTIONAL_COLS]
                logger.warning(
                    f"update_assignment({assignment_id}): columns {missing_keys} not found. "
                    "Run the SQL migration to add them. Retrying without optional columns."
                )
                core_data = {k: v for k, v in data.items() if k not in _OPTIONAL_COLS}
                rows = _rows(supabase.table(TABLE).update(core_data).eq("id", assignment_id).execute())
            else:
                raise

        if not rows:
            rows = _rows(supabase.table(TABLE).select("*").eq("id", assignment_id).execute())
        if not rows:
            raise HTTPException(status_code=500, detail="Update failed — no row returned")
        return rows[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_assignment({assignment_id}) error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─── DELETE ───────────────────────────────────────────────────────────────────

@router.delete("/{assignment_id}")
async def delete_assignment(assignment_id: int, current_user: dict = Depends(require_role('manager'))):
    try:
        _require_exists(assignment_id)
        supabase.table(TABLE).delete().eq("id", assignment_id).execute()
        return {"success": True, "detail": f"Assignment {assignment_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"delete_assignment({assignment_id}) error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
