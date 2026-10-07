# standby_rotations.py — ordered on-standby sequences: who holds each standby week,
# in what order, with which crew.
# Supabase table: standby_rotations (see supabase_migration_standby_rotations.sql)
#
# The rotation is dumb data (members in order, week length, start date); which
# member holds a given week is computed client-side (frontend
# app/standby/standbyRoster.ts) — the same split as the shifts cycle math in
# app/shifts/calcShifts.ts.
from typing import Optional, List
from datetime import date
from fastapi import Depends
from pydantic import BaseModel, Field, validator
from app.crud_router import CrudRouter
from app.supabase_client import supabase
from app.auth import get_current_user
from app.db_helpers import get_or_404

TABLE = "standby_rotations"


class CrewMember(BaseModel):
    employee_id: str = Field(..., min_length=1)
    employee_name: str = Field(..., min_length=1)
    phone: Optional[str] = None


class RotationMember(BaseModel):
    employee_id: str = Field(..., min_length=1)
    employee_name: str = Field(..., min_length=1)
    phone: Optional[str] = None
    designation: Optional[str] = None
    crew: List[CrewMember] = Field(default_factory=list)


def _check_iso_day(v: str) -> str:
    try:
        date.fromisoformat(v)
    except (TypeError, ValueError):
        raise ValueError("must be a YYYY-MM-DD date")
    return v


class StandbyRotationCreate(BaseModel):
    name: str = Field(..., min_length=1)
    section: Optional[str] = None
    members: List[RotationMember] = Field(..., min_length=1)
    week_length_days: int = Field(default=7, ge=1, le=31)
    cycle_start_date: str = Field(...)
    is_active: bool = True
    notes: Optional[str] = None

    @validator("cycle_start_date")
    def check_start_day(cls, v):
        return _check_iso_day(v)


class StandbyRotationUpdate(BaseModel):
    name: Optional[str] = None
    section: Optional[str] = None
    members: Optional[List[RotationMember]] = None
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
    StandbyRotationCreate,
    StandbyRotationUpdate,
    tags=["Standby Rotations"],
    order_by="name",
    filters={"section": "section"},
    search_columns=["name", "section"],
    not_found="Standby rotation not found",
).router


@router.get("/{rotation_id}", dependencies=[Depends(get_current_user)])
async def get_rotation(rotation_id: int):
    return get_or_404(supabase, TABLE, rotation_id, detail="Standby rotation not found")
