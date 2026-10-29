# leaves.py – simplified, no department/manager, with error logging
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import date, datetime
from app.clock import today_harare
from app.supabase_client import supabase, rows, one_row
from app.auth import get_current_user, get_current_user_optional, require_role, require_role_if_status_in
from app.db_helpers import fetch_all_pages, get_or_404
from app.leave_days import calculate_total_days
from app.errors import server_error
from app.audit import decision_stamp, has_columns
from app.concurrency import assert_unchanged, touch
from app.scope import apply_list_scope, assert_row_visible, filter_rows, person_ids_for, row_visible, stamp_create, viewer_scope
from app.leave_conflicts import INACTIVE_STATUSES as LEAVE_INACTIVE, conflict_message as leave_conflict_message, overlapping_leave
import logging
import traceback

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------- Models ----------
class LeaveCreate(BaseModel):
    employee_id: str = Field(...)
    employee_name: str = Field(...)
    position: str = Field(...)
    contact_number: str = Field(...)
    leave_type: str = Field(...)
    start_date: date = Field(...)
    end_date: date = Field(...)
    reason: str = Field(..., min_length=1)
    overtime_id: Optional[int] = None  # set only by take-as-leave: the approved lieu OT this leave pays off
    emergency_contact: Optional[str] = None
    handover_to: Optional[str] = None
    notes: Optional[str] = None
    exclude_weekends_holidays: bool = False

    @validator('end_date')
    def end_date_after_start_date(cls, v, values):
        if 'start_date' in values and v < values['start_date']:
            raise ValueError('End date must be after start date')
        return v

class LeaveUpdate(BaseModel):
    leave_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    reason: Optional[str] = None
    emergency_contact: Optional[str] = None
    handover_to: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    exclude_weekends_holidays: Optional[bool] = None
    # The updated_at the edit form was opened with; a save is refused if the record has changed since (app/concurrency.py).
    expected_updated_at: Optional[str] = None

    @validator('end_date')
    def end_date_after_start_date(cls, v, values):
        if v is not None and values.get('start_date') is not None and v < values['start_date']:
            raise ValueError('End date must be after start date')
        return v

class LeaveResponse(BaseModel):
    id: int
    employee_id: str
    employee_name: str
    position: str
    contact_number: str
    leave_type: str
    start_date: date
    end_date: date
    total_days: int
    exclude_weekends_holidays: bool = False
    reason: str
    emergency_contact: Optional[str]
    handover_to: Optional[str]
    status: str
    applied_date: datetime
    updated_at: Optional[datetime]
    notes: Optional[str]
    overtime_id: Optional[int] = None
    # Who decided it and when: stamped by the server from the signed-in session (columns exist once the integrity migration has run).
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }


class BulkLeaveStatusUpdate(BaseModel):
    ids: List[int] = Field(..., min_items=1)
    status: str

    @validator('status')
    def status_must_be_approval(cls, v):
        if v not in ('approved', 'rejected'):
            raise ValueError('status must be approved or rejected')
        return v


class BulkLeaveStatusResponse(BaseModel):
    succeeded: int
    failed: int
    updated: List[Dict[str, Any]]

# ---------- Helper ----------
def get_supabase_data(response):
    if hasattr(response, 'data'):
        return response.data
    return response

_DECISION_COLUMNS = ("approved_by", "approved_at", "rejected_by", "rejected_at")


def _decision_fields(status: Any, user: Dict[str, Any]) -> Dict[str, Any]:
    """Approver/rejecter stamps from the session, once the columns exist (so the code is safe before and after the migration)."""
    stamp = decision_stamp(status, user)
    return stamp if stamp and has_columns(supabase, "leaves", _DECISION_COLUMNS) else {}


# ---------- POST create leave ----------
@router.post("", response_model=LeaveResponse)
@router.post("/", response_model=LeaveResponse)
async def create_leave(leave: LeaveCreate, current_user: dict = Depends(get_current_user)):
    if viewer_scope(current_user) is not None:
        stamp_create(current_user, "leaves", {"employee_id": leave.employee_id},
                     supabase, person_ids_for(supabase, current_user))
    try:
        # The form only warns; this is the check that holds for a second laptop or a stale page.
        clash = overlapping_leave(supabase, leave.employee_id, leave.start_date, leave.end_date)
        if clash:
            raise HTTPException(status_code=409, detail=leave_conflict_message(clash))

        total_days = calculate_total_days(
            leave.start_date, leave.end_date, leave.exclude_weekends_holidays,
        )

        data_to_insert = {
            "employee_id": leave.employee_id,
            "employee_name": leave.employee_name,
            "position": leave.position,
            "contact_number": leave.contact_number,
            "leave_type": leave.leave_type,
            "start_date": leave.start_date.isoformat(),
            "end_date": leave.end_date.isoformat(),
            "total_days": total_days,
            "exclude_weekends_holidays": leave.exclude_weekends_holidays,
            "reason": leave.reason,
            "emergency_contact": leave.emergency_contact,
            "handover_to": leave.handover_to,
            "notes": leave.notes,
            "status": "pending",
            "applied_date": datetime.utcnow().isoformat(),
        }

        if leave.overtime_id is not None:
            data_to_insert["overtime_id"] = leave.overtime_id
        result = supabase.table("leaves").insert(data_to_insert).execute()
        created = one_row(result)
        if not created:
            raise server_error(RuntimeError("Supabase returned no row for the insert"), "Creating leave")
        return created
    except Exception as e:
        logger.error(f"Error creating leave: {str(e)}\n{traceback.format_exc()}")
        raise server_error(e, "Creating leave")

# ---------- GET all leaves ----------
@router.get("", response_model=List[LeaveResponse])
@router.get("/", response_model=List[LeaveResponse])
async def get_leaves(status: Optional[str] = None, leave_type: Optional[str] = None, overtime_id: Optional[int] = None, employee_id: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    """Leaves, newest application first. `employee_id` narrows to one person (their record in Personnel)."""
    person_ids = person_ids_for(supabase, current_user)
    try:
        def _query():
            query = supabase.table("leaves").select("*")
            if status:
                query = query.eq("status", status)
            if leave_type:
                query = query.eq("leave_type", leave_type)
            if overtime_id is not None:
                query = query.eq("overtime_id", overtime_id)
            if employee_id:
                query = query.eq("employee_id", employee_id)
            query = apply_list_scope(query, current_user, "leaves", supabase, person_ids)
            # Stable ordering prevents records with the same application date
            # from drifting between pages while the register is read.
            return query.order("applied_date", desc=True).order("id", desc=True)

        return fetch_all_pages(lambda start, end: _query().range(start, end).execute(), extract_rows=rows)
    except Exception as e:
        logger.error(f"Error fetching leaves: {str(e)}\n{traceback.format_exc()}")
        raise server_error(e, "Loading leaves")

# ---------- GET leave stats ----------
@router.get("/stats/summary")  # deliberately open — aggregate counts, not individual records
async def get_leave_stats(current_user: Optional[dict] = Depends(get_current_user_optional)):
    # Open as before; a signed-in scoped viewer counts their people only.
    try:
        response = supabase.table("leaves").select("*").execute()
        data = filter_rows(current_user, "leaves", rows(response), supabase,
                           person_ids_for(supabase, current_user))
        today = today_harare().isoformat()
        total = len(data)
        pending = sum(1 for l in data if l.get('status') == 'pending')
        approved = sum(1 for l in data if l.get('status') == 'approved')
        rejected = sum(1 for l in data if l.get('status') == 'rejected')
        on_leave_now = sum(1 for l in data if l.get('status') == 'approved' and l.get('start_date') <= today <= l.get('end_date'))
        upcoming = sum(1 for l in data if l.get('status') == 'approved' and l.get('start_date') > today)
        return {
            "total": total,
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "on_leave_now": on_leave_now,
            "upcoming": upcoming
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}\n{traceback.format_exc()}")
        # Was returning an all-zero 200 here — currently dormant (the frontend computes
        # these stats client-side instead of calling this endpoint) but a landmine for
        # whoever wires a dashboard widget to it next.
        raise server_error(e, "Loading leave stats")

# POST bulk approve/reject — one round trip instead of N PATCHes from the browser.
@router.post("/bulk-status", response_model=BulkLeaveStatusResponse)
async def bulk_update_leave_status(
    body: BulkLeaveStatusUpdate,
    authorization: Optional[str] = Header(None),
    current_user: dict = Depends(get_current_user),
):
    await require_role_if_status_in(
        body.status, {'approved', 'rejected'}, 'manager', authorization, context="Bulk leave approval",
    )
    try:
        ids = list(dict.fromkeys(body.ids))
        requested = len(ids)
        if viewer_scope(current_user) is not None:
            person_ids = person_ids_for(supabase, current_user)
            targets = rows(supabase.table("leaves").select("id,employee_id,status").in_("id", ids).execute())
            ids = [t["id"] for t in targets if row_visible(current_user, "leaves", t, supabase, person_ids)]
        data_to_update: Dict[str, Any] = {"status": body.status, **_decision_fields(body.status, current_user)}
        if ids:
            response = (
                supabase.table("leaves")
                .update(data_to_update)
                .in_("id", ids)
                .eq("status", "pending")
                .execute()
            )
            updated = response.data or []
        else:
            updated = []
        succeeded = len(updated)
        failed = max(0, requested - succeeded)
        return BulkLeaveStatusResponse(succeeded=succeeded, failed=failed, updated=updated)
    except Exception as e:
        logger.error(f"Error bulk-updating leave status: {str(e)}\n{traceback.format_exc()}")
        raise server_error(e, "Bulk-updating leaves")


# ---------- GET leave by id ----------
@router.get("/{leave_id}", response_model=LeaveResponse)
async def get_leave(leave_id: int, current_user: dict = Depends(get_current_user)):
    try:
        row = get_or_404(supabase, "leaves", leave_id, detail="Leave not found")
        return assert_row_visible(current_user, "leaves", row, supabase,
                                  person_ids_for(supabase, current_user), detail="Leave not found")
    except Exception as e:
        logger.error(f"Error fetching leave {leave_id}: {str(e)}\n{traceback.format_exc()}")
        raise server_error(e, "Loading leave")

# ---------- PATCH update leave ----------
@router.patch("/{leave_id}", response_model=LeaveResponse)
async def update_leave(leave_id: int, updated: LeaveUpdate, authorization: Optional[str] = Header(None), current_user: dict = Depends(get_current_user)):
    # Any edit requires a signed-in user (current_user); approve/reject additionally
    # requires manager+ (checked below against the same Authorization header).
    await require_role_if_status_in(updated.status, {'approved', 'rejected'}, 'manager', authorization, context="Leave approval")
    # Once a request has been decided, changing it in any way (editing it, or reopening it to pending) is a manager's call;
    # a pending request can still be amended by whoever is signed in. Checked before the try block so the refusal stays a 403.
    decided = one_row(supabase.table("leaves").select("id,status,employee_id").eq("id", leave_id).execute())
    if viewer_scope(current_user) is not None:
        assert_row_visible(current_user, "leaves", decided, supabase,
                           person_ids_for(supabase, current_user),
                           detail=f"Leave with ID {leave_id} not found")
    if decided and decided.get("status") in {"approved", "rejected"}:
        await require_role('manager')(authorization)
    try:
        existing = get_or_404(supabase, "leaves", leave_id, detail=f"Leave with ID {leave_id} not found")

        data_to_update = updated.model_dump(exclude_unset=True)
        assert_unchanged(existing, data_to_update.pop('expected_updated_at', None))

        if (
            'start_date' in data_to_update
            or 'end_date' in data_to_update
            or 'exclude_weekends_holidays' in data_to_update
        ):
            start = data_to_update.get('start_date', date.fromisoformat(existing['start_date']))
            end = data_to_update.get('end_date', date.fromisoformat(existing['end_date']))
            if isinstance(start, str):
                start = date.fromisoformat(start)
            if isinstance(end, str):
                end = date.fromisoformat(end)
            exclude = data_to_update.get(
                'exclude_weekends_holidays',
                existing.get('exclude_weekends_holidays', False),
            )
            # Changing only one date can leave the pair inverted (the model only compares dates sent together).
            if end < start:
                raise HTTPException(status_code=422, detail="The leave cannot end before it starts.")
            data_to_update['total_days'] = calculate_total_days(start, end, exclude)

            # Moving the dates, or reopening a rejected request, must not land on another live request of this person.
            becomes_status = str(data_to_update.get('status', existing.get('status')) or 'pending').lower()
            if becomes_status not in LEAVE_INACTIVE:
                clash = overlapping_leave(supabase, existing.get('employee_id'), start, end, ignore_id=leave_id)
                if clash:
                    raise HTTPException(status_code=409, detail=leave_conflict_message(clash))

        if 'start_date' in data_to_update and isinstance(data_to_update['start_date'], date):
            data_to_update['start_date'] = data_to_update['start_date'].isoformat()
        if 'end_date' in data_to_update and isinstance(data_to_update['end_date'], date):
            data_to_update['end_date'] = data_to_update['end_date'].isoformat()

        reopened = str(existing.get('status') or 'pending').lower() in LEAVE_INACTIVE and str(data_to_update.get('status') or '').lower() not in LEAVE_INACTIVE | {''}
        if reopened and not ('start_date' in data_to_update or 'end_date' in data_to_update):
            clash = overlapping_leave(supabase, existing.get('employee_id'), existing.get('start_date'), existing.get('end_date'), ignore_id=leave_id)
            if clash:
                raise HTTPException(status_code=409, detail=leave_conflict_message(clash))

        if not data_to_update:
            return existing

        data_to_update.update(_decision_fields(data_to_update.get('status'), current_user))
        touch(existing, data_to_update)
        result = supabase.table("leaves").update(data_to_update).eq("id", leave_id).execute()
        updated_row = one_row(result)
        if not updated_row:
            fetch_resp = supabase.table("leaves").select("*").eq("id", leave_id).execute()
            fetched_row = one_row(fetch_resp)
            if not fetched_row:
                raise server_error(RuntimeError("Supabase returned no row for the update"), "Updating leave")
            return fetched_row
        return updated_row
    except Exception as e:
        logger.error(f"Error updating leave {leave_id}: {str(e)}\n{traceback.format_exc()}")
        raise server_error(e, "Updating leave")

# ---------- DELETE leave ----------
@router.delete("/{leave_id}")
async def delete_leave(leave_id: int, current_user: dict = Depends(require_role('manager'))):
    try:
        existing_resp = supabase.table("leaves").select("id,status,employee_id").eq("id", leave_id).execute()
        found = one_row(existing_resp)
        if found is None:
            raise HTTPException(status_code=404, detail=f"Leave with ID {leave_id} not found")
        if viewer_scope(current_user) is not None:
            assert_row_visible(current_user, "leaves", found, supabase,
                               person_ids_for(supabase, current_user),
                               detail=f"Leave with ID {leave_id} not found")
        if str(found.get("status") or "pending").lower() == "approved":
            raise HTTPException(status_code=409, detail="An approved leave cannot be deleted: it may already have been counted. Reject it instead; that keeps the history.")
        supabase.table("leaves").delete().eq("id", leave_id).execute()
        return {"success": True, "detail": f"Leave {leave_id} deleted"}
    except Exception as e:
        logger.error(f"Error deleting leave {leave_id}: {str(e)}\n{traceback.format_exc()}")
        raise server_error(e, "Deleting leave")
