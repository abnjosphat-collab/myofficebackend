# duty_rotations.py — ordered repeating sequences of duty officials: who answers
# for the mine (or one department) each stint, in what order.
# Supabase table: duty_rotations (see supabase_migration_duty_rotations.sql)
#
# The rotation is dumb data (members in order, week length, start date); which
# member holds a given date is computed client-side (frontend
# app/standby/standbyRoster.ts) — the same split as the shifts cycle math in
# app/shifts/calcShifts.ts. One-off duty_roster entries override the rotation
# where their dates overlap; stand-ins live in rotation_covers.
from typing import Optional, List
from datetime import date
from fastapi import Depends
from pydantic import BaseModel, Field, validator
from app.crud_router import CrudRouter
from app.supabase_client import supabase
from app.auth import get_current_user
from app.db_helpers import get_or_404

TABLE = "duty_rotations"


class DutyMember(BaseModel):
    employee_id: str = Field(..., min_length=1)
    employee_name: str = Field(..., min_length=1)
    phone: Optional[str] = None
    designation: Optional[str] = None


def _check_iso_day(v: str) -> str:
    try:
        date.fromisoformat(v)
    except (TypeError, ValueError):
        raise ValueError("must be a YYYY-MM-DD date")
    return v


class DutyRotationCreate(BaseModel):
    name: str = Field(..., min_length=1)
    department: Optional[str] = None
    members: List[DutyMember] = Field(..., min_length=1)
    week_length_days: int = Field(default=7, ge=1, le=31)
    cycle_start_date: str = Field(...)
    is_active: bool = True
    notes: Optional[str] = None

    @validator("cycle_start_date")
    def check_start_day(cls, v):
        return _check_iso_day(v)


class DutyRotationUpdate(BaseModel):
    name: Optional[str] = None
    department: Optional[str] = None
    members: Optional[List[DutyMember]] = None
    week_length_days: Optional[int] = Field(None, ge=1, le=31)
    cycle_start_date: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None

    @validator("members")
    def check_members_not_emptied(cls, v):
        if v is not None and len(v) == 0:
            raise ValueError("members must not be empty")
        return v

    @validator("cycle_start_date")
    def check_start_day(cls, v):
        if v is not None:
            _check_iso_day(v)
        return v


router = CrudRouter(
    TABLE,
    DutyRotationCreate,
    DutyRotationUpdate,
    tags=["Duty Rotations"],
    order_by="name",
    filters={"department": "department"},
    search_columns=["name", "department"],
    not_found="Duty rotation not found",
).router


@router.get("/{rotation_id}", dependencies=[Depends(get_current_user)])
async def get_rotation(rotation_id: int):
    return get_or_404(supabase, TABLE, rotation_id, detail="Duty rotation not found")
