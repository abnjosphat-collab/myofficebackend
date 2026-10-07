# duty_roster.py — Duty Official roster: who answers for the mine on each date range.
# Supabase table: duty_roster (see supabase_migration_duty_roster.sql)
#
# Scope rule: two entries clash only within the same scope — mine-wide entries
# (department null) compete with mine-wide entries, department entries with the
# same department (case-insensitive). A mine-wide official and a department
# official may cover the same dates: escalation chain, not a clash.
#
# Hand-rolled instead of CrudRouter (named reason): the overlap invariant needs
# an item_id-aware cross-row check on POST and PATCH, which CrudRouter's
# dict->dict hooks can't express (they never see the URL id).

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any
from datetime import date, datetime
import logging

from app.supabase_client import supabase, rows, one_row
from app.auth import get_current_user, require_role
from app.db_helpers import fetch_all_pages, get_or_404

logger = logging.getLogger(__name__)

router = APIRouter()

TABLE = "duty_roster"


class DutyRosterCreate(BaseModel):
    employee_id: str = Field(..., min_length=1)
    employee_name: str = Field(..., min_length=1)
    phone: Optional[str] = None
    department: Optional[str] = None
    date_from: date = Field(...)
    date_to: date = Field(...)
    note: Optional[str] = None

    @validator("date_to")
    def end_not_before_start(cls, v, values):
        if "date_from" in values and v < values["date_from"]:
            raise ValueError("date_to must not be before date_from")
        return v


class DutyRosterUpdate(BaseModel):
    employee_id: Optional[str] = None
    employee_name: Optional[str] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    note: Optional[str] = None

    @validator("date_to")
    def end_not_before_start(cls, v, values):
        if v is not None and values.get("date_from") is not None and v < values["date_from"]:
            raise ValueError("date_to must not be before date_from")
        return v


def _scope(department: Optional[str]) -> Optional[str]:
    dept = (department or "").strip().lower()
    return dept or None


def _clean_department(department: Optional[str]) -> Optional[str]:
    dept = (department or "").strip()
    return dept or None


def _as_day(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")[:10]


def _find_overlap(
    department: Optional[str], date_from: str, date_to: str, exclude_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    want = _scope(department)

    def _query():
        return supabase.table(TABLE).select("id,employee_name,department,date_from,date_to")

    existing = fetch_all_pages(lambda start, end: _query().range(start, end).execute(), extract_rows=rows)
    for row in existing:
        if exclude_id is not None and row.get("id") == exclude_id:
            continue
        if _scope(row.get("department")) != want:
            continue
        start = _as_day(row.get("date_from"))
        end = _as_day(row.get("date_to"))
        if start and end and date_from <= end and start <= date_to:
            return row
    return None


def _reject_overlap(
    department: Optional[str], date_from: str, date_to: str, exclude_id: Optional[int] = None
) -> None:
    clash = _find_overlap(department, date_from, date_to, exclude_id)
    if clash:
        scope = f"department {clash.get('department')}" if _scope(clash.get("department")) else "mine-wide"
        raise HTTPException(
            status_code=409,
            detail=(
                f"{clash.get('employee_name')} is already the {scope} duty official "
                f"over {_as_day(clash.get('date_from'))} to {_as_day(clash.get('date_to'))}."
            ),
        )


@router.get("")
async def list_duty_roster(current_user: dict = Depends(get_current_user)):
    try:
        def _query():
            return supabase.table(TABLE).select("*").order("date_from").order("id")

        return fetch_all_pages(lambda start, end: _query().range(start, end).execute(), extract_rows=rows)
    except Exception as e:
        logger.error(f"Error listing duty roster: {e}")
        raise HTTPException(status_code=500, detail="Failed to list the duty roster")


@router.get("/{entry_id}")
async def get_duty_entry(entry_id: int, current_user: dict = Depends(get_current_user)):
    try:
        return get_or_404(supabase, TABLE, entry_id, detail="Duty roster entry not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching duty roster entry {entry_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch the duty roster entry")


@router.post("")
async def create_duty_entry(body: DutyRosterCreate, current_user: dict = Depends(get_current_user)):
    try:
        department = _clean_department(body.department)
        date_from = body.date_from.isoformat()
        date_to = body.date_to.isoformat()
        _reject_overlap(department, date_from, date_to)

        payload = body.dict()
        payload["department"] = department
        payload["date_from"] = date_from
        payload["date_to"] = date_to

        response = supabase.table(TABLE).insert(payload).execute()
        created = one_row(response)
        if not created:
            raise HTTPException(status_code=500, detail="Create failed")
        return created
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating duty roster entry: {e}")
        raise HTTPException(status_code=500, detail="Failed to create the duty roster entry")


@router.patch("/{entry_id}")
async def update_duty_entry(entry_id: int, body: DutyRosterUpdate, current_user: dict = Depends(get_current_user)):
    try:
        existing = get_or_404(supabase, TABLE, entry_id, detail="Duty roster entry not found")
        data = body.dict(exclude_unset=True)
        if not data:
            return existing

        if "department" in data:
            data["department"] = _clean_department(data["department"])
        for key in ("date_from", "date_to"):
            if isinstance(data.get(key), date):
                data[key] = data[key].isoformat()

        department = data.get("department", existing.get("department"))
        date_from = _as_day(data.get("date_from", existing.get("date_from")))
        date_to = _as_day(data.get("date_to", existing.get("date_to")))
        if date_from > date_to:
            raise HTTPException(status_code=422, detail="date_to must not be before date_from")
        _reject_overlap(department, date_from, date_to, exclude_id=entry_id)

        data["updated_at"] = datetime.utcnow().isoformat()
        response = supabase.table(TABLE).update(data).eq("id", entry_id).execute()
        updated = one_row(response)
        if not updated:
            raise HTTPException(status_code=500, detail="Update failed")
        return updated
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating duty roster entry {entry_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update the duty roster entry")


@router.delete("/{entry_id}")
async def delete_duty_entry(entry_id: int, current_user: dict = Depends(require_role("manager"))):
    try:
        get_or_404(supabase, TABLE, entry_id, detail="Duty roster entry not found")
        supabase.table(TABLE).delete().eq("id", entry_id).execute()
        return {"success": True, "message": "Duty roster entry deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting duty roster entry {entry_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete the duty roster entry")
