# backend/app/routes/maintenance.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from app.supabase_client import supabase, rows, one_row
from app.auth import get_current_user, require_role
from app.cache import cached, cache_get, cache_set, build_key, invalidate_namespace
from app.serialization import convert_dates_to_iso
from app.aggregation import count_by
from app.db_helpers import fetch_all_pages, get_or_404
from app.maintenance_events import append_event, diff_changes
from app.maintenance_rules import (
    COMPLETED, IN_PROGRESS, allowed_moves, describe, find_move, is_signature, missing_permit_references, normalise_permits,
)
from app.auth import role_at_least
from app.maintenance_registers import ASSIGNMENT_FIELDS, people_on_leave, refuse_people_on_leave, tools_feed
import logging
import json
import re

logger = logging.getLogger(__name__)
router = APIRouter()

# ==================== WORK ORDERS MODELS ====================
class JobType(BaseModel):
    operational: bool = False
    maintenance: bool = False
    mining: bool = False

class ManpowerRow(BaseModel):
    grade: Optional[str] = None
    required_number: Optional[str] = None  # Made optional
    required_unit_time: Optional[str] = None  # Made optional
    total_man_hours: Optional[str] = None  # Made optional

def _clean_permits(v):
    """Shared validator: the permits object is checked and cleaned (unknown permit keys are refused)."""
    try:
        return normalise_permits(v)
    except ValueError as err:
        raise ValueError(str(err))


class WorkOrderCreate(BaseModel):
    # Header Information
    to_department: str
    to_section: str
    date_raised: date
    work_order_number: str
    from_department: str
    from_section: str
    time_raised: str
    account_number: str
    equipment_info: str
    user_lab_today: str
    
    # Job Type
    job_type: JobType
    job_request_details: str
    requested_by: str
    authorising_foreman: str
    authorising_engineer: str
    allocated_to: str
    estimated_hours: str
    responsible_foreman: str
    job_instructions: str
    
    # Manpower - Made optional with default
    manpower: Optional[List[ManpowerRow]] = None
    
    # Work Analysis
    work_done_details: str
    cause_of_failure: str
    delay_details: str
    
    # Sign-off
    artisan_name: str
    artisan_sign: str
    artisan_date: str
    foreman_name: str
    foreman_sign: str
    foreman_date: str
    
    # Time Tracking
    time_work_started: str
    time_work_finished: str
    total_time_worked: str
    overtime_start_time: str
    overtime_end_time: str
    overtime_hours: str
    delay_from_time: str
    delay_to_time: str
    total_delay_hours: str
    
    # Frontend compatibility fields
    title: Optional[str] = None
    description: Optional[str] = None
    status: str = "pending"
    permits: Optional[Dict[str, Any]] = None

    @field_validator("permits")
    @classmethod
    def _permits_shape(cls, v):
        return None if v is None else _clean_permits(v)

    priority: str = "medium"
    department: Optional[str] = None
    equipment: Optional[str] = None
    due_date: Optional[date] = None
    progress: int = 0
    notes: Optional[str] = None

    # Classification & analysis fields (previously stored only in browser localStorage —
    # now persisted server-side; requires the matching columns from migration
    # 2026-07_work_orders_classification.sql).
    classification: Optional[str] = None
    classification_custom: Optional[str] = None
    failure_mode: Optional[str] = None
    discipline: Optional[str] = None
    trade: Optional[str] = None
    # Unlike the fields above, the DB column is NOT NULL DEFAULT '[]'::jsonb
    # (supabase_migration_work_orders_classification.sql). work_order.dict()
    # below (create_work_order) sends every Optional field's None through as
    # an explicit JSON null, which overrides a column default and fails the
    # NOT NULL constraint — manpower needed the identical fix a few lines
    # down for the same reason. Defaulting to [] here avoids needing another
    # one-off patch in the handler.
    spares_used: List[Dict[str, Any]] = Field(default_factory=list)

class WorkOrderUpdate(BaseModel):
    to_department: Optional[str] = None
    to_section: Optional[str] = None
    date_raised: Optional[date] = None
    work_order_number: Optional[str] = None
    from_department: Optional[str] = None
    from_section: Optional[str] = None
    time_raised: Optional[str] = None
    account_number: Optional[str] = None
    equipment_info: Optional[str] = None
    user_lab_today: Optional[str] = None
    job_type: Optional[JobType] = None
    job_request_details: Optional[str] = None
    requested_by: Optional[str] = None
    authorising_foreman: Optional[str] = None
    authorising_engineer: Optional[str] = None
    allocated_to: Optional[str] = None
    estimated_hours: Optional[str] = None
    responsible_foreman: Optional[str] = None
    job_instructions: Optional[str] = None
    manpower: Optional[List[ManpowerRow]] = None
    work_done_details: Optional[str] = None
    cause_of_failure: Optional[str] = None
    delay_details: Optional[str] = None
    artisan_name: Optional[str] = None
    artisan_sign: Optional[str] = None
    artisan_date: Optional[str] = None
    foreman_name: Optional[str] = None
    foreman_sign: Optional[str] = None
    foreman_date: Optional[str] = None
    time_work_started: Optional[str] = None
    time_work_finished: Optional[str] = None
    total_time_worked: Optional[str] = None
    overtime_start_time: Optional[str] = None
    overtime_end_time: Optional[str] = None
    overtime_hours: Optional[str] = None
    delay_from_time: Optional[str] = None
    delay_to_time: Optional[str] = None
    total_delay_hours: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    permits: Optional[Dict[str, Any]] = None

    @field_validator("permits")
    @classmethod
    def _permits_shape(cls, v):
        return None if v is None else _clean_permits(v)

    priority: Optional[str] = None
    department: Optional[str] = None
    equipment: Optional[str] = None
    due_date: Optional[date] = None
    progress: Optional[int] = None
    notes: Optional[str] = None
    classification: Optional[str] = None
    classification_custom: Optional[str] = None
    failure_mode: Optional[str] = None
    discipline: Optional[str] = None
    trade: Optional[str] = None
    spares_used: Optional[List[Dict[str, Any]]] = None
    # The row version the editor loaded. When sent, the write is refused with 409 if someone saved first.
    # Optional so callers that predate the audit trail keep working (R38); it is never stored as a column
    # value (the database bumps it).
    version: Optional[int] = None


class WorkOrderCommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("A comment cannot be empty")
        return v

# ==================== PPE MODELS (if not already separate) ====================
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

# ==================== UTILITY FUNCTIONS ====================
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)

# Columns that are date/time types in Supabase — empty strings must become NULL
_DATE_FIELDS = {'date_raised', 'artisan_date', 'foreman_date', 'due_date'}
_TIME_FIELDS = {
    'time_raised', 'time_work_started', 'time_work_finished',
    'overtime_start_time', 'overtime_end_time',
    'delay_from_time', 'delay_to_time',
}

def prepare_data_for_db(data: dict) -> dict:
    """Prepare data for Supabase insert/update.

    - date/datetime objects → ISO strings
    - dicts/lists → kept as-is (Supabase handles JSONB natively; do NOT stringify)
    - empty strings in date or time columns → None (NULL), so PostgreSQL doesn't reject them
    """
    result = {}
    for key, value in data.items():
        if isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        elif isinstance(value, (dict, list)):
            # Pass Python objects directly — PostgREST serialises to JSONB automatically
            result[key] = value
        elif key in _DATE_FIELDS | _TIME_FIELDS and value == '':
            result[key] = None
        else:
            result[key] = value
    return result

def prepare_data_for_response(data: dict) -> dict:
    """Convert JSON strings back to objects for API response"""
    result = {}
    json_fields = ['job_type', 'manpower', 'spares_used']
    
    for key, value in data.items():
        if key in json_fields and value and isinstance(value, str):
            try:
                result[key] = json.loads(value)
            except json.JSONDecodeError:
                result[key] = value
        else:
            result[key] = value
    return result

# ==================== WORK ORDERS ENDPOINTS ====================
@router.get("/work-orders", dependencies=[Depends(get_current_user)])
async def get_work_orders(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    department: Optional[str] = None,
    allocated_to: Optional[str] = None,
    to_department: Optional[str] = None,
    # A caller that only needs the N most recent rows (e.g. a sidebar activity
    # feed) can ask for them directly instead of downloading the entire table
    # and slicing client-side.
    limit: Optional[int] = None,
):
    cache_key = build_key(
        "work_orders", status=status, priority=priority, department=department,
        allocated_to=allocated_to, to_department=to_department, limit=limit,
    )
    cached_result = await cache_get(cache_key)
    if cached_result is not None:
        return cached_result
    try:
        query = supabase.table("work_orders").select("*")

        if status and status != 'all':
            query = query.eq("status", status)
        if priority and priority != 'all':
            query = query.eq("priority", priority)
        if department and department != 'all':
            query = query.eq("department", department)
        if allocated_to and allocated_to != 'all':
            query = query.eq("allocated_to", allocated_to)
        if to_department and to_department != 'all':
            query = query.eq("to_department", to_department)

        query = query.order("created_at", desc=True).order("id", desc=True)
        if limit:
            records = rows(query.limit(limit).execute())
        else:
            # PostgREST cuts a plain select at 1,000 rows, which would silently drop the oldest
            # work orders from the register once the table grows past that; page through it.
            records = fetch_all_pages(lambda start, end: query.range(start, end).execute(), extract_rows=rows)
        processed_records = []
        for record in records:
            processed_record = prepare_data_for_response(record)
            processed_records.append(processed_record)

        await cache_set(cache_key, processed_records, ttl=60, namespace="work_orders")
        return processed_records

    except Exception as e:
        logger.error(f"Error fetching work orders: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching work orders: {str(e)}")

def _generate_wo_number(offset: int = 0) -> str:
    """Next work-order number, server-side: WO-<max trailing digits + 1 + offset>, 5-wide.
    Matches the frontend format; the server is the source of truth (the client's number is
    only optimistic). `offset` steps past a number a concurrent create just took."""
    resp = supabase.table("work_orders").select("work_order_number").execute()
    max_n = 0
    for row in rows(resp):
        m = re.search(r'(\d+)$', row.get("work_order_number") or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"WO-{str(max_n + 1 + offset).zfill(5)}"


def _is_unique_violation(err: Exception) -> bool:
    """True if the DB rejected the insert for the work_order_number unique index."""
    s = str(err).lower()
    return '23505' in s or 'duplicate key' in s or 'uq_work_orders_number' in s or 'unique constraint' in s


@router.post("/work-orders")
async def create_work_order(work_order: WorkOrderCreate, current_user: dict = Depends(get_current_user)):
    try:
        data_to_insert = work_order.dict()

        # Set default title and description if not provided
        if not data_to_insert.get('title'):
            data_to_insert['title'] = data_to_insert['job_request_details'][:50] + '...' if len(data_to_insert['job_request_details']) > 50 else data_to_insert['job_request_details']

        if not data_to_insert.get('description'):
            data_to_insert['description'] = data_to_insert['job_request_details']

        if not data_to_insert.get('department'):
            data_to_insert['department'] = data_to_insert['to_department']

        if not data_to_insert.get('equipment'):
            data_to_insert['equipment'] = data_to_insert['equipment_info']

        # Handle optional manpower - ensure it's not None
        if data_to_insert.get('manpower') is None:
            data_to_insert['manpower'] = []

        # Someone on approved leave cannot be put on the job, even when the name is typed in by hand.
        refuse_people_on_leave(supabase, data_to_insert)

        # Prepare data for database
        data_to_insert = prepare_data_for_db(data_to_insert)
        data_to_insert["created_at"] = datetime.utcnow().isoformat()
        data_to_insert["updated_at"] = datetime.utcnow().isoformat()

        # Allocate the WO number server-side and insert with retry: if a concurrent create
        # grabbed the same number, the unique index (uq_work_orders_number) rejects this
        # insert — we regenerate the next free number and retry instead of erroring or
        # silently duplicating. Backed by supabase_migration_work_order_number_unique.sql.
        for attempt in range(6):
            data_to_insert["work_order_number"] = _generate_wo_number(offset=attempt)
            try:
                response = supabase.table("work_orders").insert(data_to_insert).execute()
            except Exception as insert_err:
                if not _is_unique_violation(insert_err):
                    raise
                logger.warning(f"WO number {data_to_insert['work_order_number']} taken, retrying: {insert_err}")
                if attempt < 5:
                    continue
                # Exhausted every retry on nothing but number collisions — break instead
                # of re-raising so the graceful 409 below actually runs. Previously this
                # branch was dead: the last attempt always re-raised the raw duplicate-key
                # exception straight into the generic except below, surfacing an opaque
                # 500 with a Postgres error message instead of the intended "please retry".
                break
            created = one_row(response)
            if created is not None:
                result = prepare_data_for_response(created)
                append_event(
                    supabase, entity="work_order", entity_id=created["id"], action="created",
                    user=current_user, entity_number=created.get("work_order_number"),
                    to_status=created.get("status"),
                )
                await invalidate_namespace("work_orders")
                return result
            raise HTTPException(status_code=500, detail="Failed to create work order")

        raise HTTPException(status_code=409, detail="Could not allocate a unique work order number — please retry.")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating work order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating work order: {str(e)}")

@router.get("/work-orders/{work_order_id}", dependencies=[Depends(get_current_user)])
async def get_work_order(work_order_id: int):
    try:
        row = get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found")
        return prepare_data_for_response(row)


    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching work order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching work order: {str(e)}")

@router.patch("/work-orders/{work_order_id}")
async def update_work_order(work_order_id: int, updated: WorkOrderUpdate, current_user: dict = Depends(get_current_user)):
    try:
        before = dict(get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found"))

        # exclude_unset, not "drop every None": fields the client didn't send are
        # omitted either way (identical behaviour for normal partial updates), but
        # a field the client explicitly sent as null now actually clears instead of
        # being silently ignored. Without this there is no way to clear a due date —
        # the save would report success and change nothing.
        data_to_update = updated.model_dump(exclude_unset=True)
        # The version is a precondition, not a value to store.
        expected_version = data_to_update.pop("version", None)
        # Only a name that is being set or changed is checked, so an old record can still be saved.
        refuse_people_on_leave(supabase, {
            f: data_to_update[f] for f in ASSIGNMENT_FIELDS
            if f in data_to_update and data_to_update[f] != before.get(f)
        })
        data_to_update = prepare_data_for_db(data_to_update)
        data_to_update["updated_at"] = datetime.utcnow().isoformat()

        # Shadow mode for the lifecycle rules: a status edit the new rules would refuse is still performed
        # (existing callers keep working) and is logged, so the rules can be enforced once the screens use /transition.
        new_status = data_to_update.get("status")
        if new_status and new_status != before.get("status"):
            move = find_move(before.get("status"), new_status)
            if move is None or not role_at_least(current_user.get("role", "viewer"), move.min_role):
                logger.warning("shadow_refusal: work order %s status %s -> %s by %s not allowed by the lifecycle rules",
                               work_order_id, before.get("status"), new_status, current_user.get("email"))

        # Optimistic concurrency. Only enforced when the editor sent the version it loaded AND the
        # database has the column (supabase_migration_maintenance_audit.sql); callers that send no
        # version behave exactly as before.
        guard_version = expected_version is not None and before.get("version") is not None
        if guard_version and before["version"] != expected_version:
            raise _version_conflict(before)

        query = supabase.table("work_orders").update(data_to_update).eq("id", work_order_id)
        if guard_version:
            query = query.eq("version", expected_version)
        response = query.execute()

        saved = one_row(response)
        if saved is None:
            if guard_version:
                # Passed the check above but someone saved between the read and the write.
                current = supabase.table("work_orders").select("*").eq("id", work_order_id).execute()
                raise _version_conflict(one_row(current) or before)
            raise HTTPException(status_code=500, detail="Update failed")

        changes = diff_changes(before, saved, fields=data_to_update.keys())
        if changes:
            append_event(
                supabase, entity="work_order", entity_id=work_order_id, action="updated",
                user=current_user, entity_number=saved.get("work_order_number") or before.get("work_order_number"),
                from_status=before.get("status") if "status" in changes else None,
                to_status=saved.get("status") if "status" in changes else None,
                changes=changes,
            )
        result = prepare_data_for_response(saved)
        await invalidate_namespace("work_orders")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating work order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating work order: {str(e)}")


def _version_conflict(current: dict) -> HTTPException:
    """409 carrying the row as it is now, so the editor can show what changed and keep its own typing."""
    return HTTPException(
        status_code=409,
        detail={
            "code": "version_conflict",
            "message": "This work order was changed by someone else since you opened it.",
            "current": prepare_data_for_response(current),
        },
    )


@router.delete("/work-orders/{work_order_id}")
async def delete_work_order(work_order_id: int, current_user: dict = Depends(require_role('manager'))):
    try:
        existing = get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found")

        supabase.table("work_orders").delete().eq("id", work_order_id).execute()
        append_event(
            supabase, entity="work_order", entity_id=work_order_id, action="deleted",
            user=current_user, entity_number=existing.get("work_order_number"),
            from_status=existing.get("status"),
        )
        await invalidate_namespace("work_orders")
        return {"success": True, "message": "Work order deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting work order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting work order: {str(e)}")

@router.get("/work-orders/{work_order_id}/events", dependencies=[Depends(get_current_user)])
async def get_work_order_events(work_order_id: int):
    """The audit trail of one work order, newest first.

    A work order created before the trail began simply has no rows; the screen says so. A failed read is a
    real error (500 or the upstream status), never an empty list.
    """
    try:
        resp = (
            supabase.table("maintenance_events").select("*")
            .eq("entity", "work_order").eq("entity_id", work_order_id)
            .order("created_at", desc=True).order("id", desc=True)
            .execute()
        )
        return rows(resp)
    except Exception as e:
        logger.error(f"Error fetching work order events: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching work order events: {str(e)}")


@router.get("/work-orders/{work_order_id}/comments", dependencies=[Depends(get_current_user)])
async def get_work_order_comments(work_order_id: int):
    """Comments on a work order, oldest first (a conversation reads downwards)."""
    try:
        resp = (
            supabase.table("work_order_comments").select("*")
            .eq("work_order_id", work_order_id)
            .order("created_at").order("id")
            .execute()
        )
        return rows(resp)
    except Exception as e:
        logger.error(f"Error fetching work order comments: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching work order comments: {str(e)}")


@router.post("/work-orders/{work_order_id}/comments", status_code=201)
async def add_work_order_comment(
    work_order_id: int,
    comment: WorkOrderCommentCreate,
    current_user: dict = Depends(require_role("user")),
):
    """Add a comment to a work order. Any signed-in user above viewer may comment."""
    try:
        wo = get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found")
        resp = supabase.table("work_order_comments").insert({
            "work_order_id": work_order_id,
            "body": comment.body,
            "author_user_id": current_user.get("user_id"),
            "author_name": current_user.get("email") or "",
        }).execute()
        created = one_row(resp)
        if created is None:
            raise HTTPException(status_code=500, detail="Failed to save comment")
        append_event(
            supabase, entity="work_order", entity_id=work_order_id, action="commented",
            user=current_user, entity_number=wo.get("work_order_number"), note=comment.body[:500],
        )
        return created
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding work order comment: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error adding work order comment: {str(e)}")


# ==================== LIFECYCLE ====================
class WorkOrderTransition(BaseModel):
    """Move a work order to another status."""
    to: str
    version: Optional[int] = None
    reason: Optional[str] = Field(default=None, max_length=1000)
    artisan_sign: Optional[str] = None


class WorkOrderSignoff(BaseModel):
    """The foreman's signature on a completed work order."""
    foreman_sign: str
    version: Optional[int] = None
    note: Optional[str] = Field(default=None, max_length=1000)


def _lifecycle_columns(before: dict, **values: Any) -> dict:
    """Only the lifecycle columns the table has, so the moves still work before supabase_migration_maintenance_lifecycle.sql is applied."""
    return {k: v for k, v in values.items() if k in before}


def _guarded_update(work_order_id: int, before: dict, values: dict, version: Optional[int]) -> dict:
    """Update one work order only if nobody changed it since it was read. Raises 409 version_conflict otherwise."""
    expected = version if version is not None else None
    guard = expected is not None and before.get("version") is not None
    if guard and before["version"] != expected:
        raise _version_conflict(before)
    query = supabase.table("work_orders").update(values).eq("id", work_order_id)
    if guard:
        query = query.eq("version", expected)
    saved = one_row(query.execute())
    if saved is None:
        if guard:
            current = supabase.table("work_orders").select("*").eq("id", work_order_id).execute()
            raise _version_conflict(one_row(current) or before)
        raise HTTPException(status_code=500, detail="Update failed")
    return saved


@router.get("/work-orders/{work_order_id}/transitions", dependencies=[Depends(get_current_user)])
async def get_work_order_transitions(work_order_id: int, current_user: dict = Depends(get_current_user)):
    """The status moves open to the signed-in user for this work order, and what each one needs."""
    try:
        wo = get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found")
        return [describe(m) for m in allowed_moves(wo.get("status"), current_user.get("role", "viewer"))]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading work order transitions: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error loading work order transitions: {str(e)}")


@router.post("/work-orders/{work_order_id}/transition")
async def transition_work_order(
    work_order_id: int,
    body: WorkOrderTransition,
    current_user: dict = Depends(require_role("user")),
):
    """Move a work order along its lifecycle (see ``app/maintenance_rules.py``).

    The reason, the artisan's signature and the permit references are required where the rule says so.
    A move to the status it already has is a no-op that returns the work order unchanged.
    """
    try:
        before = dict(get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found"))
        if before.get("status") == body.to:
            return prepare_data_for_response(before)

        move = find_move(before.get("status"), body.to)
        allowed = allowed_moves(before.get("status"), current_user.get("role", "viewer"))
        if move is None:
            raise HTTPException(status_code=409, detail={
                "code": "transition_not_allowed",
                "message": f"A work order that is {before.get('status') or 'new'} cannot be moved to {body.to}.",
                "allowed": [describe(m) for m in allowed],
            })
        if move not in allowed:
            raise HTTPException(status_code=403, detail={
                "code": "forbidden_role",
                "message": f"Moving a work order to {body.to} needs the {move.min_role} role.",
            })
        reason = (body.reason or "").strip()
        if move.needs_reason and not reason:
            raise HTTPException(status_code=422, detail={"code": "reason_required", "message": f"Give a reason for moving this work order to {body.to}."})
        if move.needs_signature and not is_signature(body.artisan_sign):
            raise HTTPException(status_code=422, detail={"code": "signature_required", "message": "The artisan's signature is needed to complete a work order."})
        if move.checks_permits:
            missing = missing_permit_references(before.get("permits"))
            if missing:
                raise HTTPException(status_code=422, detail={
                    "code": "permit_reference_missing",
                    "message": f"Add the reference for {', '.join(missing)} before starting this job.",
                    "permits": missing,
                })

        now = datetime.utcnow().isoformat()
        values: Dict[str, Any] = {"status": move.to, "updated_at": now}
        if move.to == IN_PROGRESS and not before.get("started_at"):
            values.update(_lifecycle_columns(before, started_at=now))
        if move.to == COMPLETED:
            values.update(artisan_sign=body.artisan_sign, progress=100)
            values.update(_lifecycle_columns(before, completed_at=now, artisan_signed_by=current_user.get("email"), artisan_signed_at=now))
        if move.clears_signoff:
            values.update(foreman_sign="")
            values.update(_lifecycle_columns(before, foreman_signed_by=None, foreman_signed_at=None, completed_at=None))

        saved = _guarded_update(work_order_id, before, values, body.version)
        append_event(
            supabase, entity="work_order", entity_id=work_order_id, action="transition", user=current_user,
            entity_number=saved.get("work_order_number") or before.get("work_order_number"),
            from_status=before.get("status"), to_status=move.to, note=reason or None,
            signature=body.artisan_sign if move.needs_signature else None,
        )
        await invalidate_namespace("work_orders")
        return prepare_data_for_response(saved)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error moving work order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error moving work order: {str(e)}")


@router.post("/work-orders/{work_order_id}/signoff")
async def sign_off_work_order(
    work_order_id: int,
    body: WorkOrderSignoff,
    current_user: dict = Depends(require_role("manager")),
):
    """The foreman signs off a completed work order. This is a fact beside ``completed``, not a new status."""
    try:
        before = dict(get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found"))
        if before.get("status") != COMPLETED:
            raise HTTPException(status_code=409, detail={"code": "transition_not_allowed", "message": "Only a completed work order can be signed off."})
        if not is_signature(body.foreman_sign):
            raise HTTPException(status_code=422, detail={"code": "signature_required", "message": "The foreman's signature is needed."})
        now = datetime.utcnow().isoformat()
        values = {"foreman_sign": body.foreman_sign, "foreman_date": now[:10], "updated_at": now}
        values.update(_lifecycle_columns(before, foreman_signed_by=current_user.get("email"), foreman_signed_at=now))
        saved = _guarded_update(work_order_id, before, values, body.version)
        append_event(
            supabase, entity="work_order", entity_id=work_order_id, action="signed_off", user=current_user,
            entity_number=saved.get("work_order_number") or before.get("work_order_number"),
            note=(body.note or "").strip() or None, signature=body.foreman_sign,
        )
        await invalidate_namespace("work_orders")
        return prepare_data_for_response(saved)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error signing off work order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error signing off work order: {str(e)}")


# ==================== REGISTERS AND TOOLS ====================
class WorkOrderToolIn(BaseModel):
    """One tool needed for a job. A null register number is a free-text tool that is not in the register."""
    tool_register_number: Optional[str] = Field(default=None, max_length=80)
    tool_name: str = Field(..., min_length=1, max_length=200)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("tool_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("tool_name must not be blank")
        return v.strip()


class WorkOrderToolsReplace(BaseModel):
    tools: List[WorkOrderToolIn] = Field(default_factory=list, max_length=50)


@router.get("/registers/tools", dependencies=[Depends(get_current_user)])
async def get_tools_register():
    """Read-only feed of the Tools & Equipment register for the tool picker.

    The Tools workspace has its own sign-in, so a maintenance page cannot call it. This reads the same
    tables with the backend's own client, behind the normal MyOffice sign-in, and changes nothing.
    """
    return tools_feed(supabase)


@router.get("/registers/leave", dependencies=[Depends(get_current_user)])
async def get_people_on_leave(on: Optional[date] = None):
    """People on approved leave on a day (today when ``on`` is omitted), for greying them out in pickers."""
    return people_on_leave(supabase, on)


@router.get("/work-orders/{work_order_id}/tools", dependencies=[Depends(get_current_user)])
async def get_work_order_tools(work_order_id: int):
    """The tools a work order needs, in the order they were added."""
    try:
        get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found")
        resp = (
            supabase.table("work_order_tools").select("*")
            .eq("work_order_id", work_order_id).order("id", desc=False).execute()
        )
        return rows(resp)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading work order tools: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error loading work order tools: {str(e)}")


@router.put("/work-orders/{work_order_id}/tools")
async def replace_work_order_tools(
    work_order_id: int,
    body: WorkOrderToolsReplace,
    current_user: dict = Depends(require_role("user")),
):
    """Replace the tools list of a work order. Maintenance only records the need; custody stays in /tools."""
    try:
        wo = get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found")

        seen = set()
        wanted = []
        for tool in body.tools:
            number = (tool.tool_register_number or "").strip() or None
            if number:
                if number in seen:
                    continue
                seen.add(number)
            wanted.append({
                "work_order_id": work_order_id,
                "tool_register_number": number,
                "tool_name": tool.tool_name,
                "note": (tool.note or "").strip() or None,
                "added_by": current_user.get("user_id"),
            })

        before = rows(
            supabase.table("work_order_tools").select("*")
            .eq("work_order_id", work_order_id).order("id", desc=False).execute()
        )
        supabase.table("work_order_tools").delete().eq("work_order_id", work_order_id).execute()
        saved = []
        try:
            for row in wanted:
                created = one_row(supabase.table("work_order_tools").insert(row).execute())
                if created is None:
                    raise RuntimeError("insert returned no row")
                saved.append(created)
        except Exception as write_err:
            # Put the previous list back so a failed save never leaves the job with no tools.
            logger.error(f"Saving tools for work order {work_order_id} failed, restoring: {write_err}")
            supabase.table("work_order_tools").delete().eq("work_order_id", work_order_id).execute()
            for old in before:
                supabase.table("work_order_tools").insert({k: v for k, v in old.items() if k not in ("id", "created_at")}).execute()
            raise HTTPException(status_code=500, detail="The tools could not be saved; the previous list was kept.")

        def label(tool: dict) -> str:
            return tool.get("tool_register_number") or tool.get("tool_name") or ""

        old_names, new_names = [label(t) for t in before], [label(t) for t in saved]
        if old_names != new_names:
            append_event(
                supabase, entity="work_order", entity_id=work_order_id, action="updated",
                user=current_user, entity_number=wo.get("work_order_number"),
                changes={"tools": [old_names, new_names]},
            )
        return saved
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving work order tools: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error saving work order tools: {str(e)}")


@router.get("/work-orders/allocated/{allocated_to}", dependencies=[Depends(get_current_user)])
async def get_work_orders_by_allocated(allocated_to: str):
    try:
        response = supabase.table("work_orders").select("*").eq("allocated_to", allocated_to).order("created_at", desc=True).execute()

        records = rows(response)
        processed_records = []
        for record in records:
            processed_record = prepare_data_for_response(record)
            processed_records.append(processed_record)
            
        return processed_records
        
    except Exception as e:
        logger.error(f"Error fetching work orders by allocated: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching work orders by allocated: {str(e)}")

# ==================== WORK ORDERS STATISTICS ====================
@router.get("/work-orders/stats/summary")
@cached("work_orders", ttl=60)
async def get_work_order_stats():
    try:
        # Get total records count
        records_response = supabase.table("work_orders").select("id", count="exact").execute()
        total_records = len(rows(records_response))

        # Get records by status + priority (one query — both come off the same rows)
        status_priority_response = supabase.table("work_orders").select("status, priority").execute()
        status_priority_rows = rows(status_priority_response)
        status_counts = count_by(status_priority_rows, 'status')
        priority_counts = count_by(status_priority_rows, 'priority')

        # Count overdue work orders
        today = date.today()
        records_all = supabase.table("work_orders").select("due_date, status").execute()
        overdue_count = 0

        for record in rows(records_all):
            due_date_str = record.get('due_date')
            status = record.get('status', 'pending')

            if due_date_str and status != 'completed':
                try:
                    due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                    if due_date < today:
                        overdue_count += 1
                except (ValueError, TypeError):
                    continue

        # Calculate average progress
        progress_response = supabase.table("work_orders").select("progress").execute()
        total_progress = 0
        count_with_progress = 0

        for record in rows(progress_response):
            progress = record.get('progress', 0)
            if progress is not None:
                total_progress += progress
                count_with_progress += 1
        
        avg_progress = round(total_progress / count_with_progress) if count_with_progress > 0 else 0
        
        return {
            "total_records": total_records,
            "status_breakdown": status_counts,
            "priority_breakdown": priority_counts,
            "overdue_count": overdue_count,
            "average_progress": avg_progress,
            "pending": status_counts.get('pending', 0),
            "in_progress": status_counts.get('in-progress', 0),
            "completed": status_counts.get('completed', 0),
            "on_hold": status_counts.get('on-hold', 0),
            "urgent": priority_counts.get('urgent', 0),
            "high": priority_counts.get('high', 0),
            "medium": priority_counts.get('medium', 0),
            "low": priority_counts.get('low', 0)
        }
        
    except Exception as e:
        logger.error(f"Error fetching work order stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching work order stats: {str(e)}")

# ==================== PPE ENDPOINTS (if you want them consolidated here) ====================
@router.get("/ppe", dependencies=[Depends(get_current_user)])
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

        records = rows(response)
        for record in records:
            convert_dates_to_iso(record)

        return records

    except Exception as e:
        logger.error(f"Error fetching PPE records: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching PPE records: {str(e)}")

@router.post("/ppe")
async def create_ppe_record(record: PPEIssueCreate, current_user: dict = Depends(get_current_user)):
    try:
        data_to_insert = record.dict()
        
        if data_to_insert.get('issue_date'):
            data_to_insert['issue_date'] = data_to_insert['issue_date'].isoformat()
        if data_to_insert.get('expiry_date'):
            data_to_insert['expiry_date'] = data_to_insert['expiry_date'].isoformat()
            
        data_to_insert["created_at"] = datetime.utcnow().isoformat()
        
        response = supabase.table("ppe_records").insert(data_to_insert).execute()

        result = one_row(response)
        if result is not None:
            convert_dates_to_iso(result)
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to create PPE record")
            
    except Exception as e:
        logger.error(f"Error creating PPE record: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating PPE record: {str(e)}")

# ==================== MAINTENANCE DASHBOARD STATS ====================
@router.get("/dashboard/stats", dependencies=[Depends(get_current_user)])
async def get_maintenance_dashboard_stats():
    """Combined stats for maintenance dashboard"""
    try:
        # Get work order stats
        work_order_stats = await get_work_order_stats()
        
        # Get PPE stats (you can add PPE stats here too)
        ppe_response = supabase.table("ppe_records").select("id", count="exact").execute()
        total_ppe = len(rows(ppe_response))
        
        # Calculate overall efficiency
        total_work_orders = work_order_stats["total_records"]
        completed_work_orders = work_order_stats["completed"]
        efficiency = round((completed_work_orders / total_work_orders * 100)) if total_work_orders > 0 else 0
        
        return {
            "work_orders": work_order_stats,
            "ppe_count": total_ppe,
            "overall_efficiency": efficiency,
            "total_maintenance_items": total_work_orders + total_ppe
        }
        
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching dashboard stats: {str(e)}")

# Health check endpoint
@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "maintenance"}