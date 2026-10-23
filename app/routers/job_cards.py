from typing import Optional, List, Any
from fastapi import HTTPException, Depends
from pydantic import BaseModel
from app.concurrency import assert_unchanged, touch
from app.crud_router import CrudRouter, serialize_row
from app.supabase_client import supabase, one_row
from app.db_helpers import get_or_404
from app.auth import get_current_user
from app.audit import actor_name
from datetime import datetime, timezone
from app.db_helpers import get_or_404


class JobCardCreate(BaseModel):
    job_no: str
    title: str
    equipment_id: Optional[str] = None
    equipment_name: Optional[str] = None
    type: str = "corrective"
    priority: str = "medium"
    status: str = "open"
    description: Optional[str] = None
    tasks: Optional[List[Any]] = []
    parts_used: Optional[List[Any]] = []
    labour_hours: Optional[float] = 0
    assigned_to: Optional[str] = None
    supervisor: Optional[str] = None
    section: Optional[str] = None
    scheduled_date: Optional[str] = None
    notes: Optional[str] = None
    # created_by is stamped by the server from the signed-in session (see _stamp_create below), never taken from the browser.


class JobCardUpdate(BaseModel):
    title: Optional[str] = None
    equipment_id: Optional[str] = None
    equipment_name: Optional[str] = None
    type: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    tasks: Optional[List[Any]] = None
    parts_used: Optional[List[Any]] = None
    labour_hours: Optional[float] = None
    assigned_to: Optional[str] = None
    supervisor: Optional[str] = None
    section: Optional[str] = None
    scheduled_date: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    # sign_off_by / sign_off_at are stamped by the server when the card is completed (see _stamp_update below).
    notes: Optional[str] = None
    # Optimistic concurrency: the updated_at the editor opened (popped before every write).
    expected_updated_at: Optional[str] = None


def _stamp_create(payload: dict) -> dict:
    return {**payload, "created_by": actor_name()}


def _stamp_update(payload: dict) -> dict:
    # Completing a job card is a signed act: who and when come from the session, not from what the browser sends.
    if payload.get("status") == "completed":
        return {**payload, "sign_off_by": actor_name(), "sign_off_at": datetime.now(timezone.utc).isoformat()}
    return payload


# Standard CRUD over job_cards (newest first) + a detail-view endpoint. reject_empty_update
# preserves the previous handler's 400 on a no-op PATCH.
router = CrudRouter(
    "job_cards", JobCardCreate, JobCardUpdate,
    tags=["Job Cards"],
    order_by="created_at", order_desc=True,
    filters={"status": "status", "priority": "priority", "section": "section"},
    not_found="Job card not found",
    reject_empty_update=True,
    before_create=_stamp_create,
    before_update=_stamp_update,
).router

# The generic CrudRouter PATCH is replaced below so a save from a stale copy is
# refused (F-09) — same route, guard and messages, plus the standard 409. The
# reads, create and DELETE stay generic.
router.routes[:] = [
    r for r in router.routes
    if not (getattr(r, "path", None) == "/{item_id}" and getattr(r, "methods", set()) == {"PATCH"})
]


@router.patch("/{item_id}", dependencies=[Depends(get_current_user)])
async def update_job_card(item_id: str, data: JobCardUpdate):
    """The generic PATCH plus the optimistic-concurrency guard: a save from a
    stale copy is refused with the standard 409, exactly like overtime."""
    try:
        item_key = int(item_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"Invalid id: {item_id!r}")
    payload = data.model_dump(exclude_unset=True)
    expectation = payload.pop("expected_updated_at", None)
    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")
    existing = get_or_404(supabase, "job_cards", item_key, detail="Job card not found")
    assert_unchanged(existing, expectation)
    payload = _stamp_update(payload)
    touch(existing, payload)
    updated = one_row(supabase.table("job_cards").update(payload).eq("id", item_key).execute())
    if updated is None:
        raise HTTPException(status_code=404, detail="Job card not found")
    return serialize_row(updated)


@router.get("/{jc_id}", dependencies=[Depends(get_current_user)])
async def get_job_card(jc_id: int):
    return get_or_404(supabase, "job_cards", jc_id, detail="Job card not found")
