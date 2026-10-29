from fastapi import APIRouter, HTTPException, Depends
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import datetime, date
from app.clock import today_harare
from app.supabase_client import supabase, rows, one_row
from app.auth import get_current_user, require_role
from app.serialization import convert_dates_to_iso
from app.aggregation import count_by
from app.db_helpers import fetch_all_pages, get_or_404
from app.errors import server_error
from app.concurrency import assert_unchanged, touch
from app.ppe_conflicts import duplicate_message, existing_duplicate
from app.scope import apply_list_scope, assert_row_visible, person_ids_for, viewer_scope
import logging
import json

logger = logging.getLogger(__name__)
router = APIRouter()

# Custom JSON encoder to handle date objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)

# Pydantic Models
class PPEIssueCreate(BaseModel):
    # The person copies are optional because the server stamps them from the employee register on every create
    # (client copies are ignored); only the mine number reference is required.
    employee_name: Optional[str] = None
    employee_id: str = Field(..., min_length=1)
    department: Optional[str] = None
    position: Optional[str] = None
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
    # This issue replaces an item that failed before its expiry; failure_reason (optional) says what failed. A reason
    # without the flag is dropped, so the flag alone decides what the "Premature failures" filter shows.
    premature_failure: bool = False
    failure_reason: Optional[str] = Field(default=None, max_length=300)

    @validator('failure_reason', always=True)
    def reason_only_with_the_flag(cls, v, values):
        v = (v or '').strip() or None
        return v if values.get('premature_failure') else None

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

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
    premature_failure: Optional[bool] = None
    failure_reason: Optional[str] = Field(default=None, max_length=300)
    # The updated_at the edit form was opened with; a save is refused if the record has changed since (app/concurrency.py).
    expected_updated_at: Optional[str] = None

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

# The register stamps the person. PPE rows copy the holder's number, name, position, department and section so lists
# read without a join, but the employee register owns those values: every create and every move to another person
# overwrites the copies from the register row and ignores what the client sent. An unknown mine number is refused
# with a plain sentence, not a 500.
def _like_literal(value: str) -> str:
    """Escape the LIKE pattern characters so an ilike filter matches literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _register_employee(employee_id: str):
    """The register row for this mine number (case-insensitive exact), or None."""
    want = (employee_id or "").strip().upper()
    if not want:
        return None
    found = supabase.table("employees").select(
        "employee_id,first_name,last_name,designation,department,section"
    ).ilike("employee_id", _like_literal(employee_id.strip())).execute()
    for row in rows(found):
        if (row.get("employee_id") or "").strip().upper() == want:
            return row
    return None


def _stamp_person(data: dict, person: dict) -> dict:
    """Overwrite the person copies (and the id's spelling) from the register row."""
    data["employee_id"] = person.get("employee_id")
    data["employee_name"] = " ".join(f"{person.get('first_name') or ''} {person.get('last_name') or ''}".split())
    data["position"] = person.get("designation") or ""
    data["department"] = person.get("department") or ""
    data["mine_section"] = person.get("section") or ""
    return data


def _settle_premature(patch: dict, existing: dict) -> None:
    """Keep the premature-failure pair consistent on an edit: unticking clears the reason, a reason only stands with
    the flag (sent now or already stored), and an explicit null flag means 'not premature' (the column is NOT NULL)."""
    if "premature_failure" in patch:
        patch["premature_failure"] = bool(patch["premature_failure"])
    if "failure_reason" in patch:
        patch["failure_reason"] = (patch["failure_reason"] or "").strip() or None
    flagged = patch.get("premature_failure", bool(existing.get("premature_failure")))
    if not flagged and ("premature_failure" in patch or patch.get("failure_reason")):
        patch["failure_reason"] = None


def _resolve_person_or_422(employee_id: str) -> dict:
    person = _register_employee(employee_id)
    if person is None:
        raise HTTPException(status_code=422, detail="That mine number is not on the employee register.")
    return person

# Helper function to convert dates in records
# PPE Records Endpoints
@router.get("")
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
        query = query.order("created_at", desc=True).order("id", desc=True)
        records = await run_in_threadpool(
            lambda: fetch_all_pages(lambda start, end: query.range(start, end).execute(), extract_rows=rows)
        )
        for record in records:
            convert_dates_to_iso(record)

        return records

    except Exception as e:
        logger.error(f"Error fetching PPE records: {str(e)}")
        raise server_error(e, "Loading PPE records")

@router.post("")
async def create_ppe_record(record: PPEIssueCreate, current_user: dict = Depends(get_current_user)):
    try:
        # The person must be on the register; the row keeps the register's copies and spelling, not the client's.
        person = _resolve_person_or_422(record.employee_id)
        if viewer_scope(current_user) is not None and (person.get("section") or "") != viewer_scope(current_user):
            raise HTTPException(status_code=404, detail="Not found")

        # Convert Pydantic model to dict and handle date serialization
        data_to_insert = _stamp_person(record.model_dump(), person)

        # The same item issued to the same person on the same day twice is refused here, not just in the browser.
        clash = existing_duplicate(supabase, data_to_insert["employee_id"], record.ppe_type, record.issue_date)
        if clash:
            raise HTTPException(status_code=409, detail=duplicate_message(clash))
        
        # Ensure dates are properly formatted for database
        if data_to_insert.get('issue_date'):
            data_to_insert['issue_date'] = data_to_insert['issue_date'].isoformat()
        if data_to_insert.get('expiry_date'):
            data_to_insert['expiry_date'] = data_to_insert['expiry_date'].isoformat()
            
        data_to_insert["created_at"] = datetime.utcnow().isoformat()
        
        response = supabase.table("ppe_records").insert(data_to_insert).execute()

        result = one_row(response)
        if result is not None:
            # Convert dates to ISO format for JSON response
            convert_dates_to_iso(result)
            return result
        else:
            raise server_error(RuntimeError("Supabase returned no row for the insert"), "Creating PPE record")
            
    except Exception as e:
        logger.error(f"Error creating PPE record: {str(e)}")
        raise server_error(e, "Creating PPE record")

@router.get("/{record_id:int}")
async def get_ppe_record(record_id: int, current_user: dict = Depends(get_current_user)):
    try:
        result = get_or_404(supabase, "ppe_records", record_id, detail="PPE record not found")
        assert_row_visible(current_user, "ppe_records", result, supabase,
                           person_ids_for(supabase, current_user), detail="PPE record not found")
        convert_dates_to_iso(result)
        return result


    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching PPE record: {str(e)}")
        raise server_error(e, "Loading PPE record")

@router.patch("/{record_id}")
async def update_ppe_record(record_id: int, updated: PPEIssueUpdate, current_user: dict = Depends(get_current_user)):
    try:
        existing = get_or_404(supabase, "ppe_records", record_id, detail="PPE record not found")
        if viewer_scope(current_user) is not None:
            assert_row_visible(current_user, "ppe_records", existing, supabase,
                               person_ids_for(supabase, current_user), detail="PPE record not found")

        # exclude_unset, not a None-filter: an explicitly-sent null must clear the
        # field, not be silently dropped. See work_orders (backend edec24a).
        data_to_update = updated.model_dump(exclude_unset=True)
        assert_unchanged(existing, data_to_update.pop('expected_updated_at', None))
        _settle_premature(data_to_update, existing)

        # Moving the record to another person re-stamps the copies from the register; anything else leaves them alone.
        if "employee_id" in data_to_update:
            sent = data_to_update["employee_id"]
            if sent is not None and (sent or "").strip().upper() == (existing.get("employee_id") or "").strip().upper():
                # The same person (a re-pick that changed nothing, or only the id's case): keep the stored spelling.
                data_to_update.pop("employee_id")
            else:
                new_person = _resolve_person_or_422(sent or "")
                if viewer_scope(current_user) is not None and (new_person.get("section") or "") != viewer_scope(current_user):
                    raise HTTPException(status_code=404, detail="PPE record not found")
                _stamp_person(data_to_update, new_person)

        # Moving the item, the date or the person must not make it a duplicate of another issue.
        if any(k in data_to_update for k in ('employee_id', 'ppe_type', 'issue_date')):
            merged = {**existing, **data_to_update}
            clash = existing_duplicate(supabase, merged.get('employee_id'), merged.get('ppe_type'), merged.get('issue_date'), ignore_id=record_id)
            if clash:
                raise HTTPException(status_code=409, detail=duplicate_message(clash))

        # Convert dates to ISO format for database
        if data_to_update.get('issue_date'):
            data_to_update['issue_date'] = data_to_update['issue_date'].isoformat()
        if data_to_update.get('expiry_date'):
            data_to_update['expiry_date'] = data_to_update['expiry_date'].isoformat()
            
        touch(existing, data_to_update)
        
        response = supabase.table("ppe_records").update(data_to_update).eq("id", record_id).execute()

        result = one_row(response)
        if result is not None:
            convert_dates_to_iso(result)
            return result
        else:
            raise server_error(RuntimeError("Supabase returned no row for the update"), "Updating PPE record")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating PPE record: {str(e)}")
        raise server_error(e, "Updating PPE record")

@router.delete("/{record_id}")
async def delete_ppe_record(record_id: int, current_user: dict = Depends(require_role('manager'))):
    try:
        existing = get_or_404(supabase, "ppe_records", record_id, detail="PPE record not found")
        if viewer_scope(current_user) is not None:
            assert_row_visible(current_user, "ppe_records", existing, supabase,
                               person_ids_for(supabase, current_user), detail="PPE record not found")

        supabase.table("ppe_records").delete().eq("id", record_id).execute()
        return {"success": True, "message": "PPE record deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting PPE record: {str(e)}")
        raise server_error(e, "Deleting PPE record")

@router.get("/employee/{employee_id}")
async def get_employee_ppe_records(employee_id: str, current_user: dict = Depends(get_current_user)):
    try:
        query = apply_list_scope(
            supabase.table("ppe_records").select("*").eq("employee_id", employee_id),
            current_user, "ppe_records", supabase, person_ids_for(supabase, current_user),
        ).order("issue_date", desc=True).order("id", desc=True)
        records = await run_in_threadpool(
            lambda: fetch_all_pages(lambda start, end: query.range(start, end).execute(), extract_rows=rows)
        )
        for record in records:
            convert_dates_to_iso(record)
            
        return records
        
    except Exception as e:
        logger.error(f"Error fetching employee PPE records: {str(e)}")
        raise server_error(e, "Loading employee PPE records")

# Statistics Endpoint
@router.get("/stats/summary")
async def get_ppe_stats(current_user: dict = Depends(get_current_user)):
    person_ids = person_ids_for(supabase, current_user)
    try:
        records_all = await run_in_threadpool(
            lambda: fetch_all_pages(
                lambda start, end: apply_list_scope(
                    supabase.table("ppe_records")
                    .select("id, employee_id, status, condition, expiry_date")
                    .order("id"),
                    current_user, "ppe_records", supabase, person_ids)
                    .range(start, end)
                    .execute(),
                extract_rows=rows,
            )
        )
        total_records = len(records_all)
        status_counts = count_by(records_all, 'status')
        condition_counts = count_by(records_all, 'condition')
        
        # Count expiring soon (within 30 days) and expired
        today = today_harare()
        expiring_soon = 0
        expired = 0

        for record in records_all:
            expiry_date_str = record.get('expiry_date')
            status = record.get('status', 'active')

            if expiry_date_str and status == 'active':
                try:
                    expiry = datetime.strptime(expiry_date_str, '%Y-%m-%d').date()
                    days_until_expiry = (expiry - today).days

                    if days_until_expiry < 0:
                        expired += 1
                    elif days_until_expiry <= 30:
                        expiring_soon += 1
                except (ValueError, TypeError):
                    # Handle invalid date formats
                    continue

        unique_employees = len({record.get('employee_id') for record in records_all if record.get('employee_id')})
        
        return {
            "total_records": total_records,
            "unique_employees": unique_employees,
            "status_breakdown": status_counts,
            "condition_breakdown": condition_counts,
            "expiring_soon": expiring_soon,
            "expired": expired
        }
        
    except Exception as e:
        logger.error(f"Error fetching PPE stats: {str(e)}")
        raise server_error(e, "Loading PPE stats")


# ─── PPE replacement matrix ────────────────────────────────────────────────────
# Default months-until-expiry per item type (calculated from the issue date). Overrides
# are stored in the ppe_matrix table (see supabase_migration_ppe_matrix.sql) and merged
# over these; the endpoints degrade gracefully to defaults if that table doesn't exist.

import calendar

# interval 0 = no expiry (item doesn't expire / isn't replaced on a schedule).
PPE_MATRIX_DEFAULTS = {
    "worksuit": 6, "gumboots": 6, "safety_shoes": 6,
    "helmet": 24, "Cap_lamp_belt": 24, "pneumo_jacket": 24, "harness": 24, "safety_chain_belt": 24,
    "vest": 3, "glasses": 3, "respirator": 1, "rainsuit": 6,
    "gloves": 0, "overall": 6,
}


def _add_months(iso_date, months):
    """issue date (YYYY-MM-DD) + N months → expiry date string, clamping day overflow."""
    if not iso_date or not months:
        return None
    try:
        d = date.fromisoformat(str(iso_date)[:10])
    except Exception:
        return None
    m = d.month - 1 + int(months)
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day).isoformat()


def _matrix_overrides():
    """{ppe_type: interval_months} from the ppe_matrix table, or {} if unavailable."""
    try:
        resp = supabase.table("ppe_matrix").select("ppe_type, interval_months").execute()
        return {r["ppe_type"]: r["interval_months"] for r in rows(resp)}
    except Exception as e:
        logger.info(f"ppe_matrix table unavailable, using defaults: {e}")
        return {}


class MatrixEntry(BaseModel):
    ppe_type: str = Field(..., min_length=1)
    interval_months: int = Field(..., ge=0, le=120)   # 0 = no expiry


@router.get("/matrix", dependencies=[Depends(get_current_user)])
async def get_ppe_matrix():
    """Effective matrix: company defaults with any saved overrides applied."""
    matrix = dict(PPE_MATRIX_DEFAULTS)
    matrix.update(_matrix_overrides())
    return matrix


def _apply_matrix_to_type(ppe_type: str, interval: int) -> int:
    """Recalculate expiry = issue_date + interval for every ACTIVE record of this type,
    across all sections by design (a scoped recalc would leave rows stale against the
    same shared matrix; the caller only ever learns counts, never rows).
    overwriting whatever expiry_date is currently stored (including a manually-typed
    one — the matrix is the source of truth once a type's interval is set/changed).
    Returns the number of records updated."""
    recs = supabase.table("ppe_records").select("id, issue_date").eq("ppe_type", ppe_type).eq("status", "active").execute()
    updated = 0
    for r in rows(recs):
        # interval 0 = no expiry → clear the date; otherwise recompute from issue date.
        new_exp = None if interval == 0 else _add_months(r.get("issue_date"), interval)
        if interval == 0 or new_exp:
            supabase.table("ppe_records").update({
                "expiry_date": new_exp,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", r["id"]).execute()
            updated += 1
    return updated


@router.put("/matrix")
async def set_ppe_matrix_entry(entry: MatrixEntry, current_user: dict = Depends(require_role('manager'))):
    """Set (upsert) the interval for one PPE type, then immediately recalculate expiry
    for every active record of that type so the matrix and stored data never drift
    apart — no separate 'apply' step needed."""
    try:
        supabase.table("ppe_matrix").upsert({
            "ppe_type": entry.ppe_type,
            "interval_months": entry.interval_months,
            "updated_at": datetime.utcnow().isoformat(),
        }, on_conflict="ppe_type").execute()
    except Exception as e:
        raise server_error(e, "Saving matrix")
    try:
        updated = _apply_matrix_to_type(entry.ppe_type, entry.interval_months)
    except Exception as e:
        raise server_error(e, "Matrix saved, but recalculating existing records")
    return {"ppe_type": entry.ppe_type, "interval_months": entry.interval_months, "updated": updated}


@router.post("/matrix/{ppe_type}/apply")
async def apply_ppe_matrix(ppe_type: str, current_user: dict = Depends(require_role('manager'))):
    """Recalculate expiry for every ACTIVE record of this type against the current
    matrix interval — the manual per-type control (PUT /matrix now does this
    automatically on save, but this stays for re-running it standalone, e.g. after a
    record's issue_date was edited)."""
    overrides = _matrix_overrides()
    if ppe_type in overrides:
        interval = overrides[ppe_type]
    elif ppe_type in PPE_MATRIX_DEFAULTS:
        interval = PPE_MATRIX_DEFAULTS[ppe_type]
    else:
        raise HTTPException(status_code=400, detail=f"No interval configured for '{ppe_type}'")
    try:
        updated = _apply_matrix_to_type(ppe_type, interval)
    except Exception as e:
        raise server_error(e, "Recalculating records")
    return {"ppe_type": ppe_type, "interval_months": interval, "updated": updated}


@router.post("/matrix/apply-all")
async def apply_ppe_matrix_all(current_user: dict = Depends(require_role('manager'))):
    """Recalculate expiry for every ACTIVE record of every PPE type against the
    current matrix — a one-shot fix for records left stale by a past interval change,
    without clicking 'Recalculate' once per type."""
    matrix = dict(PPE_MATRIX_DEFAULTS)
    matrix.update(_matrix_overrides())
    results = {}
    failed = []
    for ppe_type, interval in matrix.items():
        try:
            results[ppe_type] = _apply_matrix_to_type(ppe_type, interval)
        except Exception as e:
            logger.error(f"Failed recalculating '{ppe_type}': {e}")
            results[ppe_type] = None
            failed.append(ppe_type)
    # `results[type] is None` already distinguished "failed" from "0 records updated"
    # for a caller that reads it carefully, but nothing surfaced it if they only checked
    # the aggregate `total_updated` — `failed` makes it explicit rather than implicit.
    return {"results": results, "total_updated": sum(v for v in results.values() if v), "failed": failed}
