# backend/app/routes/maintenance.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from app.supabase_client import supabase, rows, one_row
from app.auth import get_current_user, get_current_user_optional, require_role
from app.cache import cached, cache_get, cache_set, build_key, invalidate_namespace
from app.serialization import convert_dates_to_iso
from app.aggregation import count_by
from app.db_helpers import fetch_all_pages, get_or_404
from app.concurrency import assert_unchanged
from app.audit import has_columns
from app.maintenance_events import append_event, diff_changes
from app.maintenance_links import resolve_equipment_id
from app.errors import server_error
from app.scope import apply_list_scope, assert_row_visible, person_ids_for, stamp_create, viewer_scope
from app.clock import today_harare
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
    priority: str = "medium"
    department: Optional[str] = None
    section: Optional[str] = None
    equipment: Optional[str] = None
    # M1 links: the register machine and the raising schedule (columns from
    # supabase_m1_maintenance_links_20261026.sql; dropped until it has run).
    equipment_id: Optional[int] = None
    schedule_id: Optional[int] = None
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
    priority: Optional[str] = None
    department: Optional[str] = None
    section: Optional[str] = None
    equipment: Optional[str] = None
    equipment_id: Optional[int] = None
    schedule_id: Optional[int] = None
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
    # Optimistic concurrency: the updated_at the editor opened (popped before every write).
    expected_updated_at: Optional[str] = None


class WorkOrderClose(BaseModel):
    """Fast close (M2): what was done, and the actual time as start/finish times
    or plain hours. Anything the row already holds counts, so a job reported
    earlier closes with only what is missing; a breakdown also needs its cause
    and failure mode (M3 turns the mode into a managed list)."""

    work_done_details: str = ""
    time_work_started: Optional[str] = None
    time_work_finished: Optional[str] = None
    total_hours: Optional[float] = None
    artisan_name: Optional[str] = None
    artisan_date: Optional[str] = None
    cause_of_failure: Optional[str] = None
    failure_mode: Optional[str] = None
    expected_updated_at: Optional[str] = None


class WorkOrderBulkClose(WorkOrderClose):
    """Bulk close (M2): the shared fields for a batch of routine planned jobs,
    applied to every row that closes. The dialog sends only the shared note
    and time, so each row keeps its own artisan and date (blanks default to
    the session person and today, as in a single close)."""

    ids: List[int] = Field(..., min_length=1, max_length=100)


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

# M1 timestamps: when things happen, stamped by the server (never taken from
# the browser -- integrity rule 2). started_at on the first move to
# in-progress, completed_at on the move to completed (a direct pending to
# completed close fills started_at too, since the work happened), signed_off_at
# when the foreman sign first appears. Stamps are written once and never
# cleared, so reopening a job keeps its history; nothing is stamped until the
# M1 SQL has run (the columns do not exist before that).
_LIFECYCLE_COLUMNS = ("started_at", "completed_at", "signed_off_at")
_LINK_COLUMNS = ("equipment_id", "schedule_id")


def _lifecycle_stamps(before: dict, updates: dict, now: str | None = None) -> dict:
    """Server stamps for a work-order PATCH. Empty when the M1 columns are absent."""
    if not has_columns(supabase, "work_orders", _LIFECYCLE_COLUMNS):
        return {}
    now = now if now is not None else datetime.utcnow().isoformat()
    stamps: dict = {}
    new_status = updates.get("status", before.get("status"))
    if (
        new_status == "in-progress"
        and before.get("status") != "in-progress"
        and not before.get("started_at")
    ):
        stamps["started_at"] = now
    if new_status == "completed" and before.get("status") != "completed":
        if not before.get("completed_at"):
            stamps["completed_at"] = now
        if not before.get("started_at") and "started_at" not in stamps:
            stamps["started_at"] = now
    before_sign = (before.get("foreman_sign") or "").strip()
    after_sign = (updates.get("foreman_sign", before.get("foreman_sign")) or "").strip()
    if before_sign == "" and after_sign != "" and not before.get("signed_off_at"):
        stamps["signed_off_at"] = now
    return stamps


# M2 fast close: a job is finished when no further work is expected on it.
# "completed" (the artisan closed it) and the foreman sign-off are separate
# steps by owner decision; "cancelled" and "not-done" (a planned job that was
# missed) are the other terminal states. Nothing terminal is overdue, and
# nothing terminal can be closed again.
_TERMINAL_STATUSES = ("completed", "cancelled", "not-done")

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _minutes_of(value: str) -> int:
    """Minutes since midnight for a "HH:MM" time; ValueError when malformed."""
    match = _HHMM.match(value.strip())
    if not match:
        raise ValueError(f"not a HH:MM time: {value!r}")
    return int(match.group(1)) * 60 + int(match.group(2))


def _duration_minutes(start: str, finish: str) -> int:
    """Minutes from start to finish; a finish earlier than the start means the
    next day (the same rule as the frontend's durationText)."""
    mins = _minutes_of(finish) - _minutes_of(start)
    return mins + 1440 if mins < 0 else mins


def close_duration_text(start: str, finish: str) -> str:
    """A worked duration as "7h 30m", the shape total_time_worked has always had."""
    mins = _duration_minutes(start, finish)
    return f"{mins // 60}h {mins % 60:02d}m"


def hours_text(hours: float) -> str:
    """Plain hours as "7h 30m", so hours-closed jobs read the same everywhere."""
    mins = int(round(hours * 60))
    return f"{mins // 60}h {mins % 60:02d}m"


def close_problems(before: dict, close: "WorkOrderClose") -> list:
    """What is missing or contradictory in a close, as one reason per problem.

    The check runs on the merged values (what was sent, falling back to what
    the row already holds), so a job reported earlier closes with only what is
    missing. Empty means the close may go ahead.
    """
    problems = []
    done = (close.work_done_details or "").strip() or (before.get("work_done_details") or "").strip()
    if not done:
        problems.append("Say what was done.")
    if close.total_hours is not None:
        if not 0 < close.total_hours <= 1000:
            problems.append("Hours must be more than 0 (and below 1000).")
    elif close.time_work_started or close.time_work_finished:
        start = (close.time_work_started or "").strip()
        finish = (close.time_work_finished or "").strip()
        if not start or not finish:
            problems.append("Give both the start and finish times, or hours instead.")
        else:
            try:
                if _duration_minutes(start, finish) <= 0:
                    problems.append("The finish time must be after the start time.")
            except ValueError:
                problems.append("Times must look like 08:30.")
    elif not (before.get("total_time_worked") or "").strip():
        problems.append("Record the time worked: start and finish, or hours.")
    if (before.get("classification") or "") == "breakdown":
        cause = (close.cause_of_failure or "").strip() or (before.get("cause_of_failure") or "").strip()
        if not cause:
            problems.append("Say what caused the breakdown.")
        mode = (close.failure_mode or "").strip() or (before.get("failure_mode") or "").strip()
        if not mode:
            problems.append("Pick the failure mode.")
    if close.artisan_date:
        try:
            date.fromisoformat(close.artisan_date.strip())
        except ValueError:
            problems.append("The date must be YYYY-MM-DD.")
    return problems


def _close_updates(before: dict, close: "WorkOrderClose", user: dict) -> dict:
    """The row write for a validated close: completed, progress 100, the merged
    report fields. The artisan defaults to the signed-in person when no name is
    recorded anywhere; the date defaults to today (Harare)."""
    updates: dict = {"status": "completed", "progress": 100}
    if (close.work_done_details or "").strip():
        updates["work_done_details"] = close.work_done_details.strip()
    if close.total_hours is not None:
        updates["total_time_worked"] = hours_text(close.total_hours)
    elif close.time_work_started or close.time_work_finished:
        updates["time_work_started"] = (close.time_work_started or "").strip()
        updates["time_work_finished"] = (close.time_work_finished or "").strip()
        updates["total_time_worked"] = close_duration_text(
            updates["time_work_started"], updates["time_work_finished"])
    name = (
        (close.artisan_name or "").strip()
        or (before.get("artisan_name") or before.get("allocated_to") or "").strip()
        or str(user.get("name") or user.get("email") or "")
    )
    if name and name != (before.get("artisan_name") or ""):
        updates["artisan_name"] = name
    if close.artisan_date:
        updates["artisan_date"] = close.artisan_date.strip()
    elif not (before.get("artisan_date") or "").strip():
        updates["artisan_date"] = today_harare().isoformat()
    cause = (close.cause_of_failure or "").strip()
    if cause:
        updates["cause_of_failure"] = cause
    mode = (close.failure_mode or "").strip()
    if mode:
        updates["failure_mode"] = mode
    return updates


def _bulk_classification_label(row: dict) -> str:
    """How a bulk-close refusal names a job that is not a routine planned one."""
    cls = row.get("classification") or ""
    if cls == "breakdown":
        return "a breakdown"
    if cls == "project":
        return "a project job"
    if cls == "custom":
        return (row.get("classification_custom") or "").strip() or "a custom job"
    return f"classified {cls}" if cls else "unclassified"


# ==================== WORK ORDERS ENDPOINTS ====================
@router.get("/work-orders", dependencies=[Depends(get_current_user)])
async def get_work_orders(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    department: Optional[str] = None,
    allocated_to: Optional[str] = None,
    to_department: Optional[str] = None,
    equipment_id: Optional[int] = None,
    schedule_id: Optional[int] = None,
    # A caller that only needs the N most recent rows (e.g. a sidebar activity
    # feed) can ask for them directly instead of downloading the entire table
    # and slicing client-side.
    limit: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
):
    cache_key = build_key(
        "work_orders", scope=viewer_scope(current_user), status=status, priority=priority, department=department,
        allocated_to=allocated_to, to_department=to_department, limit=limit,
        equipment_id=equipment_id, schedule_id=schedule_id,
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
        if equipment_id is not None or schedule_id is not None:
            # Before the M1 SQL runs no row can carry a link, so a filtered
            # read is honestly empty (and must not 500 on a missing column).
            if not has_columns(supabase, "work_orders", _LINK_COLUMNS):
                return []
            if equipment_id is not None:
                query = query.eq("equipment_id", equipment_id)
            if schedule_id is not None:
                query = query.eq("schedule_id", schedule_id)
        query = apply_list_scope(query, current_user, "work_orders")

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
        raise server_error(e, "Loading work orders")

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
        data_to_insert = work_order.model_dump()

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

        # M1 links: resolve the typed machine name on an exact match when the
        # picker sent no equipment_id; drop both keys until the M1 SQL has run.
        if has_columns(supabase, "work_orders", _LINK_COLUMNS):
            if data_to_insert.get("equipment_id") is None:
                data_to_insert["equipment_id"] = resolve_equipment_id(
                    supabase, data_to_insert.get("equipment_info"), current_user)
        else:
            data_to_insert.pop("equipment_id", None)
            data_to_insert.pop("schedule_id", None)

        # Prepare data for database
        data_to_insert = prepare_data_for_db(data_to_insert)
        data_to_insert = stamp_create(current_user, "work_orders", data_to_insert)
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
        raise server_error(e, "Creating work order")

@router.get("/work-orders/{work_order_id}", dependencies=[Depends(get_current_user)])
async def get_work_order(work_order_id: int, current_user: dict = Depends(get_current_user)):
    try:
        row = get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found")
        assert_row_visible(current_user, "work_orders", row, detail="Work order not found")
        return prepare_data_for_response(row)


    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching work order: {str(e)}")
        raise server_error(e, "Loading work order")

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
        if viewer_scope(current_user) is not None:
            merged = {**before, **data_to_update}
            assert_row_visible(current_user, "work_orders", merged, detail="Work order not found")
        # The version is a precondition, not a value to store.
        expected_version = data_to_update.pop("version", None)
        assert_unchanged(before, data_to_update.pop("expected_updated_at", None))
        data_to_update = prepare_data_for_db(data_to_update)
        data_to_update.update(_lifecycle_stamps(before, data_to_update))
        data_to_update["updated_at"] = datetime.utcnow().isoformat()

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
        raise server_error(e, "Updating work order")


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


@router.post("/work-orders/bulk-close")
async def bulk_close_work_orders(body: WorkOrderBulkClose, current_user: dict = Depends(get_current_user)):
    """Close up to 100 routine planned jobs at once, with shared what-done/time.

    Each row is judged on its own merged values; only open, routine planned
    jobs (no classification yet, or planned_maintenance) close. The answer is
    always 200 with per-row results: {"closed": [ids], "skipped": [{id,
    reason}]}. A row that fails mid-batch is skipped with its reason, never
    reported as closed, and rows already closed keep their audit events.
    """
    closed: list = []
    skipped: list = []
    for work_order_id in body.ids:
        try:
            before = one_row(supabase.table("work_orders").select("*").eq("id", work_order_id).execute())
        except Exception as err:  # noqa: BLE001 - one unreadable row must not sink the batch
            logger.error(f"Error reading work order {work_order_id} for bulk close: {str(err)}")
            skipped.append({"id": work_order_id, "reason": "It could not be read; try again."})
            continue
        if before is None:
            skipped.append({"id": work_order_id, "reason": "It no longer exists."})
            continue
        before = dict(before)
        if viewer_scope(current_user) is not None:
            try:
                assert_row_visible(current_user, "work_orders", {**before, "status": "completed"},
                                   detail="Work order not found")
            except HTTPException:
                skipped.append({"id": work_order_id, "reason": "You can no longer see it."})
                continue
        status = before.get("status") or "pending"
        if status in _TERMINAL_STATUSES:
            skipped.append({"id": work_order_id, "reason": f"Already {status}."})
            continue
        cls = before.get("classification") or ""
        if cls not in ("", "planned_maintenance"):
            skipped.append({"id": work_order_id,
                            "reason": f"Only routine planned jobs close in bulk (this one is {_bulk_classification_label(before)})."})
            continue
        problems = close_problems(before, body)
        if problems:
            skipped.append({"id": work_order_id, "reason": " ".join(problems)})
            continue
        try:
            updates = prepare_data_for_db(_close_updates(before, body, current_user))
            updates.update(_lifecycle_stamps(before, updates))
            updates["updated_at"] = datetime.utcnow().isoformat()
            saved = one_row(supabase.table("work_orders").update(updates).eq("id", work_order_id).execute())
        except Exception as err:  # noqa: BLE001 - reported per row, below
            logger.error(f"Error closing work order {work_order_id} in bulk: {str(err)}")
            skipped.append({"id": work_order_id, "reason": "It could not be closed; try again."})
            continue
        if saved is None:
            skipped.append({"id": work_order_id, "reason": "It changed while closing; try again."})
            continue
        changes = diff_changes(before, saved, fields=updates.keys())
        append_event(
            supabase, entity="work_order", entity_id=work_order_id, action="closed",
            user=current_user, entity_number=saved.get("work_order_number") or before.get("work_order_number"),
            from_status=before.get("status"), to_status="completed", changes=changes,
        )
        closed.append(work_order_id)
    await invalidate_namespace("work_orders")
    return {"closed": closed, "skipped": skipped}


@router.post("/work-orders/{work_order_id}/close")
async def close_work_order(work_order_id: int, close: WorkOrderClose, current_user: dict = Depends(get_current_user)):
    """Close one job fast: completed, progress 100, the M1 stamps, one audit
    event. Refuses a job that is already finished (409) or that still misses
    what was done, the time, or (for a breakdown) its cause and failure mode
    (422 naming each gap). The foreman sign-off stays a separate step."""
    try:
        before = dict(get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found"))
        if viewer_scope(current_user) is not None:
            assert_row_visible(current_user, "work_orders", {**before, "status": "completed"},
                               detail="Work order not found")
        status = before.get("status") or "pending"
        if status in _TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail=f"This work order is already {status}; only an open job can be closed.")
        problems = close_problems(before, close)
        if problems:
            raise HTTPException(status_code=422, detail=" ".join(problems))
        assert_unchanged(before, close.expected_updated_at)
        updates = prepare_data_for_db(_close_updates(before, close, current_user))
        updates.update(_lifecycle_stamps(before, updates))
        updates["updated_at"] = datetime.utcnow().isoformat()

        saved = one_row(supabase.table("work_orders").update(updates).eq("id", work_order_id).execute())
        if saved is None:
            raise HTTPException(status_code=500, detail="Update failed")

        changes = diff_changes(before, saved, fields=updates.keys())
        if changes:
            append_event(
                supabase, entity="work_order", entity_id=work_order_id, action="closed",
                user=current_user, entity_number=saved.get("work_order_number") or before.get("work_order_number"),
                from_status=before.get("status"), to_status="completed", changes=changes,
            )
        result = prepare_data_for_response(saved)
        await invalidate_namespace("work_orders")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing work order: {str(e)}")
        raise server_error(e, "Closing work order")


@router.delete("/work-orders/{work_order_id}")
async def delete_work_order(work_order_id: int, current_user: dict = Depends(require_role('manager'))):
    try:
        existing = get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found")
        assert_row_visible(current_user, "work_orders", existing, detail="Work order not found")

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
        raise server_error(e, "Deleting work order")

@router.get("/work-orders/{work_order_id}/events", dependencies=[Depends(get_current_user)])
async def get_work_order_events(work_order_id: int, current_user: dict = Depends(get_current_user)):
    """The audit trail of one work order, newest first.

    A work order created before the trail began simply has no rows; the screen says so. A failed read is a
    real error (500 or the upstream status), never an empty list.
    """
    try:
        if viewer_scope(current_user) is not None:
            parent = one_row(supabase.table("work_orders").select("*").eq("id", work_order_id).execute())
            assert_row_visible(current_user, "work_orders", parent, detail="Work order not found")
        resp = (
            supabase.table("maintenance_events").select("*")
            .eq("entity", "work_order").eq("entity_id", work_order_id)
            .order("created_at", desc=True).order("id", desc=True)
            .execute()
        )
        return rows(resp)
    except Exception as e:
        logger.error(f"Error fetching work order events: {str(e)}")
        raise server_error(e, "Loading work order events")


@router.get("/work-orders/{work_order_id}/comments", dependencies=[Depends(get_current_user)])
async def get_work_order_comments(work_order_id: int, current_user: dict = Depends(get_current_user)):
    """Comments on a work order, oldest first (a conversation reads downwards)."""
    try:
        if viewer_scope(current_user) is not None:
            parent = one_row(supabase.table("work_orders").select("*").eq("id", work_order_id).execute())
            assert_row_visible(current_user, "work_orders", parent, detail="Work order not found")
        resp = (
            supabase.table("work_order_comments").select("*")
            .eq("work_order_id", work_order_id)
            .order("created_at").order("id")
            .execute()
        )
        return rows(resp)
    except Exception as e:
        logger.error(f"Error fetching work order comments: {str(e)}")
        raise server_error(e, "Loading work order comments")


@router.post("/work-orders/{work_order_id}/comments", status_code=201)
async def add_work_order_comment(
    work_order_id: int,
    comment: WorkOrderCommentCreate,
    current_user: dict = Depends(require_role("user")),
):
    """Add a comment to a work order. Any signed-in user above viewer may comment."""
    try:
        wo = get_or_404(supabase, "work_orders", work_order_id, detail="Work order not found")
        assert_row_visible(current_user, "work_orders", wo, detail="Work order not found")
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
        raise server_error(e, "Adding work order comment")


@router.get("/work-orders/allocated/{allocated_to}", dependencies=[Depends(get_current_user)])
async def get_work_orders_by_allocated(allocated_to: str, current_user: dict = Depends(get_current_user)):
    try:
        _q = apply_list_scope(supabase.table("work_orders").select("*").eq("allocated_to", allocated_to), current_user, "work_orders")
        response = _q.order("created_at", desc=True).execute()

        records = rows(response)
        processed_records = []
        for record in records:
            processed_record = prepare_data_for_response(record)
            processed_records.append(processed_record)
            
        return processed_records
        
    except Exception as e:
        logger.error(f"Error fetching work orders by allocated: {str(e)}")
        raise server_error(e, "Loading work orders by allocated")

# ==================== WORK ORDERS STATISTICS ====================
@router.get("/work-orders/stats/summary")
@cached("work_orders", ttl=60)
async def get_work_order_stats(current_user: Optional[dict] = Depends(get_current_user_optional)):
    try:
        # Get total records count
        _q = apply_list_scope(supabase.table("work_orders").select("id", count="exact"), current_user, "work_orders")
        records_response = _q.execute()
        total_records = len(rows(records_response))

        # Get records by status + priority (one query — both come off the same rows)
        _q = apply_list_scope(supabase.table("work_orders").select("status, priority"), current_user, "work_orders")
        status_priority_response = _q.execute()
        status_priority_rows = rows(status_priority_response)
        status_counts = count_by(status_priority_rows, 'status')
        priority_counts = count_by(status_priority_rows, 'priority')

        # Count overdue work orders ("today" in Africa/Harare, not server-local).
        today = today_harare()
        _q = apply_list_scope(supabase.table("work_orders").select("due_date, status"), current_user, "work_orders")
        records_all = _q.execute()
        overdue_count = 0

        for record in rows(records_all):
            due_date_str = record.get('due_date')
            status = record.get('status', 'pending')

            if due_date_str and status not in _TERMINAL_STATUSES:
                try:
                    due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                    if due_date < today:
                        overdue_count += 1
                except (ValueError, TypeError):
                    continue

        # Calculate average progress
        _q = apply_list_scope(supabase.table("work_orders").select("progress"), current_user, "work_orders")
        progress_response = _q.execute()
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
            "not_done": status_counts.get('not-done', 0),
            "urgent": priority_counts.get('urgent', 0),
            "high": priority_counts.get('high', 0),
            "medium": priority_counts.get('medium', 0),
            "low": priority_counts.get('low', 0)
        }
        
    except Exception as e:
        logger.error(f"Error fetching work order stats: {str(e)}")
        raise server_error(e, "Loading work order stats")

# ==================== PPE ENDPOINTS (if you want them consolidated here) ====================
@router.get("/ppe")
async def get_ppe_records(
    status: Optional[str] = None,
    ppe_type: Optional[str] = None,
    department: Optional[str] = None,
    location: Optional[str] = None,
    employee_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    person_ids = person_ids_for(supabase, current_user)
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

        query = apply_list_scope(query, current_user, "ppe_records", supabase, person_ids)
        response = query.order("created_at", desc=True).execute()

        records = rows(response)
        for record in records:
            convert_dates_to_iso(record)

        return records

    except Exception as e:
        logger.error(f"Error fetching PPE records: {str(e)}")
        raise server_error(e, "Loading PPE records")

@router.post("/ppe")
async def create_ppe_record(record: PPEIssueCreate, current_user: dict = Depends(get_current_user)):
    if viewer_scope(current_user) is not None:
        stamp_create(current_user, "ppe_records", {"employee_id": record.employee_id},
                     supabase, person_ids_for(supabase, current_user))
    try:
        data_to_insert = record.model_dump()
        
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
        raise server_error(e, "Creating PPE record")

# ==================== MAINTENANCE DASHBOARD STATS ====================
@router.get("/dashboard/stats", dependencies=[Depends(get_current_user)])
async def get_maintenance_dashboard_stats(current_user: dict = Depends(get_current_user)):
    """Combined stats for maintenance dashboard"""
    try:
        # Get work order stats
        work_order_stats = await get_work_order_stats(current_user=current_user)
        
        # Get PPE stats (you can add PPE stats here too)
        _ppe_q = supabase.table("ppe_records").select("id", count="exact")
        if viewer_scope(current_user) is not None:
            _ppe_q = apply_list_scope(_ppe_q, current_user, "ppe_records", supabase, person_ids_for(supabase, current_user))
        ppe_response = _ppe_q.execute()
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
        raise server_error(e, "Loading dashboard stats")

# Health check endpoint
@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "maintenance"}