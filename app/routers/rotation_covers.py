# rotation_covers.py — Stand-ins: who holds in place of whom, for which dates.
# Supabase table: rotation_covers (see supabase_migration_rotation_covers.sql)
#
# kind + rotation_id point at a standby_rotations or duty_rotations row; the
# absent member may be a rotation lead, a crew member, or a duty official.
# Invariant: one person holds at most one cover per rotation over any date —
# a second overlapping cover for the same absent person is refused with 409.
# Nobody holds in place of themselves (422).
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

TABLE = "rotation_covers"

KINDS = ("standby", "duty")


def _check_kind(v: str) -> str:
    kind = (v or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("kind must be 'standby' or 'duty'")
    return kind


class RotationCoverCreate(BaseModel):
    kind: str = Field(...)
    rotation_id: int = Field(...)
    absent_employee_id: str = Field(..., min_length=1)
    absent_employee_name: str = Field(..., min_length=1)
    cover_employee_id: str = Field(..., min_length=1)
    cover_employee_name: str = Field(..., min_length=1)
    cover_phone: Optional[str] = None
    date_from: date = Field(...)
    date_to: date = Field(...)
    reason: Optional[str] = None

    @validator("kind")
    def check_kind_value(cls, v):
        return _check_kind(v)

    @validator("date_to")
    def end_not_before_start(cls, v, values):
        if "date_from" in values and v < values["date_from"]:
            raise ValueError("date_to must not be before date_from")
        return v


class RotationCoverUpdate(BaseModel):
    kind: Optional[str] = None
    rotation_id: Optional[int] = None
    absent_employee_id: Optional[str] = None
    absent_employee_name: Optional[str] = None
    cover_employee_id: Optional[str] = None
    cover_employee_name: Optional[str] = None
    cover_phone: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    reason: Optional[str] = None

    @validator("kind")
    def check_kind_value(cls, v):
        if v is not None:
            return _check_kind(v)
        return v

    @validator("date_to")
    def end_not_before_start(cls, v, values):
        if v is not None and values.get("date_from") is not None and v < values["date_from"]:
            raise ValueError("date_to must not be before date_from")
        return v


def _who(value: Any) -> str:
    return str(value or "").strip().upper()


def _as_day(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")[:10]


def _reject_self_cover(absent_id: Any, cover_id: Any) -> None:
    if _who(absent_id) == _who(cover_id):
        raise HTTPException(
            status_code=422,
            detail="Nobody holds in place of themselves: the cover and the absent member are the same person.",
        )


def _find_overlap(
    kind: str, rotation_id: Any, absent_id: Any, date_from: str, date_to: str,
    exclude_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    want_absent = _who(absent_id)

    def _query():
        return supabase.table(TABLE).select(
            "id,kind,rotation_id,absent_employee_id,cover_employee_name,date_from,date_to"
        )

    existing = fetch_all_pages(lambda start, end: _query().range(start, end).execute(), extract_rows=rows)
    for row in existing:
        if exclude_id is not None and row.get("id") == exclude_id:
            continue
        if _check_kind(str(row.get("kind"))) != kind:
            continue
        if row.get("rotation_id") != rotation_id:
            continue
        if _who(row.get("absent_employee_id")) != want_absent:
            continue
        start = _as_day(row.get("date_from"))
        end = _as_day(row.get("date_to"))
        if start and end and date_from <= end and start <= date_to:
            return row
    return None


def _reject_overlap(
    kind: str, rotation_id: Any, absent_id: Any, absent_name: Any,
    date_from: str, date_to: str, exclude_id: Optional[int] = None,
) -> None:
    clash = _find_overlap(kind, rotation_id, absent_id, date_from, date_to, exclude_id)
    if clash:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{clash.get('cover_employee_name')} already holds in place of {absent_name} "
                f"over {_as_day(clash.get('date_from'))} to {_as_day(clash.get('date_to'))}."
            ),
        )


@router.get("")
async def list_covers(
    kind: Optional[str] = None,
    rotation_id: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
):
    try:
        def _query():
            q = supabase.table(TABLE).select("*").order("date_from").order("id")
            if kind is not None:
                q = q.eq("kind", kind)
            if rotation_id is not None:
                q = q.eq("rotation_id", rotation_id)
            return q

        return fetch_all_pages(lambda start, end: _query().range(start, end).execute(), extract_rows=rows)
    except Exception as e:
        logger.error(f"Error listing rotation covers: {e}")
        raise HTTPException(status_code=500, detail="Failed to list the rotation covers")


@router.get("/{cover_id}")
async def get_cover(cover_id: int, current_user: dict = Depends(get_current_user)):
    try:
        return get_or_404(supabase, TABLE, cover_id, detail="Rotation cover not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching rotation cover {cover_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch the rotation cover")


@router.post("")
async def create_cover(body: RotationCoverCreate, current_user: dict = Depends(get_current_user)):
    try:
        date_from = body.date_from.isoformat()
        date_to = body.date_to.isoformat()
        _reject_self_cover(body.absent_employee_id, body.cover_employee_id)
        _reject_overlap(
            body.kind, body.rotation_id, body.absent_employee_id,
            body.absent_employee_name, date_from, date_to,
        )

        payload = body.dict()
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
        logger.error(f"Error creating rotation cover: {e}")
        raise HTTPException(status_code=500, detail="Failed to create the rotation cover")


@router.patch("/{cover_id}")
async def update_cover(cover_id: int, body: RotationCoverUpdate, current_user: dict = Depends(get_current_user)):
    try:
        existing = get_or_404(supabase, TABLE, cover_id, detail="Rotation cover not found")
        data = body.dict(exclude_unset=True)
        if not data:
            return existing

        for key in ("date_from", "date_to"):
            if isinstance(data.get(key), date):
                data[key] = data[key].isoformat()

        kind = _check_kind(str(data.get("kind", existing.get("kind"))))
        rotation_id = data.get("rotation_id", existing.get("rotation_id"))
        absent_id = data.get("absent_employee_id", existing.get("absent_employee_id"))
        absent_name = data.get("absent_employee_name", existing.get("absent_employee_name"))
        cover_employee = data.get("cover_employee_id", existing.get("cover_employee_id"))
        date_from = _as_day(data.get("date_from", existing.get("date_from")))
        date_to = _as_day(data.get("date_to", existing.get("date_to")))
        if date_from > date_to:
            raise HTTPException(status_code=422, detail="date_to must not be before date_from")
        _reject_self_cover(absent_id, cover_employee)
        _reject_overlap(kind, rotation_id, absent_id, absent_name, date_from, date_to, exclude_id=cover_id)

        data["updated_at"] = datetime.utcnow().isoformat()
        response = supabase.table(TABLE).update(data).eq("id", cover_id).execute()
        updated = one_row(response)
        if not updated:
            raise HTTPException(status_code=500, detail="Update failed")
        return updated
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating rotation cover {cover_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update the rotation cover")


@router.delete("/{cover_id}")
async def delete_cover(cover_id: int, current_user: dict = Depends(require_role("manager"))):
    try:
        get_or_404(supabase, TABLE, cover_id, detail="Rotation cover not found")
        supabase.table(TABLE).delete().eq("id", cover_id).execute()
        return {"success": True, "message": "Rotation cover deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting rotation cover {cover_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete the rotation cover")
