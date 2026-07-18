# app/routers/schedules.py
# Recurring maintenance schedules, and the job that turns them into work orders.
#
# Schedules used to live in the browser's localStorage, so they were invisible
# to everyone but the person who created them and nothing could act on them
# while that browser was closed. Generation in particular cannot live in a
# client: a work order that only gets raised if somebody happens to open the
# page is not a schedule.

import os
import re
from datetime import date, datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, Field, field_validator

from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Maintenance Schedules"])

RECURRENCE_TYPES = ('daily', 'weekly', 'biweekly', 'monthly', 'quarterly', 'yearly', 'custom')
PRIORITIES = ('low', 'medium', 'high', 'urgent')
FAR_FUTURE = date(9999, 1, 1)


# ---------- Models ----------
class ScheduleBase(BaseModel):
    name: str = Field(..., min_length=1)
    equipment_info: str = ''
    to_department: str = ''
    allocated_to: str = ''
    authorising_foreman: str = ''
    estimated_hours: str = ''
    job_request_details: str = ''
    job_instructions: str = ''
    priority: str = 'medium'
    recurrence_type: str
    recurrence_dow: int = 1
    recurrence_dom: int = 1
    recurrence_months: List[int] = []
    specific_dates: List[date] = []
    advance_days: int = 0
    active: bool = True
    next_due_date: Optional[date] = None

    @field_validator('priority')
    @classmethod
    def _priority(cls, v: str) -> str:
        if v not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
        return v

    @field_validator('recurrence_type')
    @classmethod
    def _recurrence(cls, v: str) -> str:
        if v not in RECURRENCE_TYPES:
            raise ValueError(f"recurrence_type must be one of {RECURRENCE_TYPES}")
        return v


class ScheduleCreate(ScheduleBase):
    pass


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    equipment_info: Optional[str] = None
    to_department: Optional[str] = None
    allocated_to: Optional[str] = None
    authorising_foreman: Optional[str] = None
    estimated_hours: Optional[str] = None
    job_request_details: Optional[str] = None
    job_instructions: Optional[str] = None
    priority: Optional[str] = None
    recurrence_type: Optional[str] = None
    recurrence_dow: Optional[int] = None
    recurrence_dom: Optional[int] = None
    recurrence_months: Optional[List[int]] = None
    specific_dates: Optional[List[date]] = None
    advance_days: Optional[int] = None
    active: Optional[bool] = None
    next_due_date: Optional[date] = None


# ---------- Recurrence ----------
def _clamp_dom(year: int, month: int, dom: int) -> date:
    """Build a date, pulling the day back to the last valid day of the month.
    A 31st-of-the-month schedule must still fire in February."""
    if month > 12:
        year, month = year + 1, month - 12
    next_month = date(year + (month // 12), (month % 12) + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(dom, last_day))


def next_occurrence(s: dict, frm: date) -> date:
    """The next due date strictly after `frm`. Mirrors getNextOccurrence() on
    the maintenance page so the server and the UI agree on what 'next' means."""
    rt = s['recurrence_type']
    dom = s.get('recurrence_dom') or 1

    if rt == 'daily':
        return frm + timedelta(days=1)
    if rt == 'weekly':
        return frm + timedelta(days=7)
    if rt == 'biweekly':
        return frm + timedelta(days=14)
    if rt == 'monthly':
        return _clamp_dom(frm.year, frm.month + 1, dom)
    if rt == 'quarterly':
        months = sorted(s.get('recurrence_months') or [0, 3, 6, 9])
        nxt = next((m for m in months if m > frm.month - 1), None)
        if nxt is not None:
            return _clamp_dom(frm.year, nxt + 1, dom)
        return _clamp_dom(frm.year + 1, (months[0] if months else 0) + 1, dom)
    if rt == 'yearly':
        months = s.get('recurrence_months') or [0]
        return _clamp_dom(frm.year + 1, months[0] + 1, dom)
    if rt == 'custom':
        future = sorted(d for d in _as_dates(s.get('specific_dates')) if d > frm)
        return future[0] if future else FAR_FUTURE
    return FAR_FUTURE


def _as_dates(values) -> List[date]:
    out: List[date] = []
    for v in values or []:
        if isinstance(v, date):
            out.append(v)
        else:
            try:
                out.append(date.fromisoformat(str(v)))
            except ValueError:
                continue
    return out


def is_due(s: dict, today: date) -> bool:
    """True if this schedule should have raised a work order by `today`."""
    if not s.get('active') or not s.get('next_due_date'):
        return False
    due = s['next_due_date']
    if not isinstance(due, date):
        due = date.fromisoformat(str(due))
    return (due - timedelta(days=s.get('advance_days') or 0)) <= today


# ---------- Helpers ----------
def _rows(resp):
    return getattr(resp, 'data', resp) or []


def _next_work_order_number() -> str:
    """WO-00001 style, continuing the existing series.

    Racy by nature — two callers reading the same max would build the same
    number. Generation runs single-threaded from cron so this is safe today; a
    DB sequence would be the fix if work-order creation ever goes concurrent.
    """
    resp = (
        supabase.table('work_orders')
        .select('work_order_number')
        .order('id', desc=True)
        .limit(200)
        .execute()
    )
    highest = 0
    for row in _rows(resp):
        m = re.search(r'(\d+)$', row.get('work_order_number') or '')
        if m:
            highest = max(highest, int(m.group(1)))
    return f"WO-{highest + 1:05d}"


# ---------- CRUD ----------
@router.get("")
async def list_schedules(user: dict = Depends(get_current_user)):
    try:
        resp = supabase.table('maintenance_schedules').select('*').order('created_at', desc=True).execute()
        return _rows(resp)
    except Exception as e:
        logger.error(f"list_schedules failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load schedules.")


@router.post("")
async def create_schedule(payload: ScheduleCreate, user: dict = Depends(require_role('manager'))):
    body = payload.model_dump(mode='json')
    body['created_by'] = user['user_id']
    if not body.get('next_due_date'):
        body['next_due_date'] = next_occurrence(body, date.today()).isoformat()
    try:
        resp = supabase.table('maintenance_schedules').insert(body).execute()
        return _rows(resp)[0]
    except Exception as e:
        logger.error(f"create_schedule failed: {e}")
        raise HTTPException(status_code=500, detail="Could not create schedule.")


@router.patch("/{schedule_id}")
async def update_schedule(schedule_id: int, payload: ScheduleUpdate, user: dict = Depends(require_role('manager'))):
    # exclude_unset, not a None-filter: an explicitly-sent null must clear the
    # field, not be silently dropped. See work_orders (backend edec24a).
    body = payload.model_dump(mode='json', exclude_unset=True)
    if not body:
        raise HTTPException(status_code=400, detail="No fields to update.")
    try:
        resp = supabase.table('maintenance_schedules').update(body).eq('id', schedule_id).execute()
        rows = _rows(resp)
        if not rows:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        return rows[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_schedule failed: {e}")
        raise HTTPException(status_code=500, detail="Could not update schedule.")


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: int, user: dict = Depends(require_role('manager'))):
    try:
        supabase.table('maintenance_schedules').delete().eq('id', schedule_id).execute()
        return {"ok": True}
    except Exception as e:
        logger.error(f"delete_schedule failed: {e}")
        raise HTTPException(status_code=500, detail="Could not delete schedule.")


@router.get("/{schedule_id}/runs")
async def schedule_runs(schedule_id: int, user: dict = Depends(get_current_user)):
    """What this schedule has raised so far."""
    try:
        resp = (
            supabase.table('maintenance_schedule_runs')
            .select('*').eq('schedule_id', schedule_id)
            .order('due_date', desc=True).execute()
        )
        return _rows(resp)
    except Exception as e:
        logger.error(f"schedule_runs failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load schedule history.")


# ---------- Generation ----------
def _generate_one(s: dict, today: date) -> Optional[dict]:
    """Raise the work order for one due schedule, then roll it forward.

    The run row is claimed first: its UNIQUE(schedule_id, due_date) is what
    makes this idempotent. If the claim fails the work order already exists for
    that due date, so nothing is raised.
    """
    due = s['next_due_date']
    if not isinstance(due, date):
        due = date.fromisoformat(str(due))

    try:
        supabase.table('maintenance_schedule_runs').insert(
            {'schedule_id': s['id'], 'due_date': due.isoformat()}
        ).execute()
    except Exception:
        logger.info(f"schedule {s['id']} already generated for {due} — skipping")
        return None

    now = datetime.now()
    work_order = {
        'work_order_number': _next_work_order_number(),
        'title': s['name'],
        'equipment_info': s.get('equipment_info') or '',
        'to_department': s.get('to_department') or '',
        'allocated_to': s.get('allocated_to') or '',
        'authorising_foreman': s.get('authorising_foreman') or '',
        'estimated_hours': s.get('estimated_hours') or '',
        'job_request_details': s.get('job_request_details') or '',
        'job_instructions': s.get('job_instructions') or '',
        'priority': s.get('priority') or 'medium',
        'status': 'pending',
        'date_raised': today.isoformat(),
        'time_raised': now.strftime('%H:%M'),
        'due_date': due.isoformat(),
        'requested_by': 'Scheduled maintenance',
        'notes': f"Raised automatically from schedule #{s['id']} ({s['name']}), due {due.isoformat()}.",
    }

    try:
        wo = _rows(supabase.table('work_orders').insert(work_order).execute())[0]
    except Exception as e:
        # Release the claim so the next run can retry rather than silently
        # skipping this due date forever.
        supabase.table('maintenance_schedule_runs').delete() \
            .eq('schedule_id', s['id']).eq('due_date', due.isoformat()).execute()
        logger.error(f"schedule {s['id']}: work order insert failed, claim released: {e}")
        raise

    supabase.table('maintenance_schedule_runs').update({'work_order_id': wo['id']}) \
        .eq('schedule_id', s['id']).eq('due_date', due.isoformat()).execute()

    upcoming = next_occurrence(s, due)
    supabase.table('maintenance_schedules').update({
        'next_due_date': upcoming.isoformat(),
        'last_generated': now.isoformat(),
    }).eq('id', s['id']).execute()

    return {'schedule_id': s['id'], 'due_date': due.isoformat(),
            'work_order_id': wo['id'], 'work_order_number': wo['work_order_number'],
            'next_due_date': upcoming.isoformat()}


@router.post("/generate")
async def generate_due_work_orders(
    x_cron_secret: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Raise work orders for every schedule that has come due.

    Two ways in: the cron secret (Render Cron has no user session), or a signed-in
    manager pressing the button. Safe to call repeatedly — see _generate_one.
    """
    secret = os.getenv('CRON_SECRET')
    authorised = bool(secret and x_cron_secret and x_cron_secret == secret)
    if not authorised:
        await require_role('manager')(authorization)

    today = date.today()
    try:
        resp = supabase.table('maintenance_schedules').select('*').eq('active', True).execute()
        schedules = _rows(resp)
    except Exception as e:
        logger.error(f"generate: could not load schedules: {e}")
        raise HTTPException(status_code=500, detail="Could not load schedules.")

    created, failed = [], []
    for s in schedules:
        if not is_due(s, today):
            continue
        try:
            result = _generate_one(s, today)
            if result:
                created.append(result)
        except Exception as e:
            # One bad schedule must not stop the rest of the run.
            failed.append({'schedule_id': s['id'], 'error': str(e)})

    logger.info(f"generate: {len(created)} work order(s) raised, {len(failed)} failed")
    return {'generated': len(created), 'created': created, 'failed': failed}
