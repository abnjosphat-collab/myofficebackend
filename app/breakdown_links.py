# app/breakdown_links.py
"""One entry for breakdowns (M4): a breakdown and its work order, linked both ways.

A breakdown holds the downtime (start, end, response, repair); the work order
holds the job and its cost. Each breakdown links to at most one work order via
``breakdowns.work_order_id`` (``supabase_m4_breakdown_work_order_20261026.sql``,
nullable, ``ON DELETE SET NULL``); the reverse direction — a work order's
breakdown — is read back through the same column, so there is one source of
truth and the two can never disagree.

Until the M4 SQL has run the column does not exist: raising still creates the
work order (a job is useful on its own), but no link is stored and the reverse
read answers None. A breakdown *shell* (the record a breakdown work order
raises for its downtime) is skipped entirely before the SQL runs — a shell
with no link is exactly the double entry M4 removes.
"""
from typing import Any, Optional

from app.audit import has_columns
from app.clock import today_harare
from app.scope import apply_list_scope

#: The link column on breakdowns (added by the M4 SQL file).
WORK_ORDER_LINK_COLUMNS = ("work_order_id",)


def has_work_order_link(client: Any) -> bool:
    """Whether breakdowns can link to work orders right now (the M4 column exists)."""
    return has_columns(client, "breakdowns", WORK_ORDER_LINK_COLUMNS)


def find_breakdown_for_work_order(client: Any, work_order_id: Any, user: Any = None) -> Optional[dict]:
    """The breakdown linked to a work order, newest first when several point at it.

    None when the M4 column does not exist yet, or when nothing is linked. A
    section-scoped viewer only sees a link it can see (user=None reads
    globally, for the exactly-one-shell rule every writer meets).
    """
    if not has_work_order_link(client):
        return None
    query = client.table("breakdowns").select("*").eq("work_order_id", work_order_id)
    query = apply_list_scope(query, user, "breakdowns")
    resp = query.order("id", desc=True).limit(1).execute()
    data = getattr(resp, "data", resp) or []
    return dict(data[0]) if data else None


def link_breakdown_to_work_order(client: Any, breakdown_id: Any, work_order_id: Any) -> Optional[dict]:
    """Point a breakdown at its work order. None when the M4 column is absent."""
    if not has_work_order_link(client):
        return None
    resp = (
        client.table("breakdowns")
        .update({"work_order_id": work_order_id})
        .eq("id", breakdown_id)
        .execute()
    )
    data = getattr(resp, "data", resp) or []
    return dict(data[0]) if data else None


#: Breakdown priorities are critical/high/medium/low; work orders say urgent
#: instead of critical. Anything unknown falls back to medium.
_BREAKDOWN_PRIORITY_TO_WO = {"critical": "urgent", "high": "high", "medium": "medium", "low": "low"}


def work_order_values_for_breakdown(breakdown: dict, user: dict) -> dict:
    """The work-order fields a breakdown carries over when it raises its job.

    The caller allocates the WO number and inserts (through the maintenance
    create path, so numbering, machine linking, stamps and the audit event
    stay in one place). Pure: no database reads.
    """
    machine = (breakdown.get("machine_name") or "").strip()
    description = (breakdown.get("breakdown_description") or breakdown.get("machine_description") or "").strip()
    artisan = (breakdown.get("artisan_name") or "").strip()
    department = (breakdown.get("department") or "").strip() or "Engineering"
    priority = _BREAKDOWN_PRIORITY_TO_WO.get((breakdown.get("priority") or "").strip().lower(), "medium")
    reporter = str(user.get("name") or user.get("email") or "")
    raised = (breakdown.get("breakdown_date") or "")[:10] or today_harare().isoformat()
    return {
        "equipment_info": machine,
        "to_department": department,
        "allocated_to": artisan,
        "artisan_name": artisan,
        "priority": priority,
        "classification": "breakdown",
        "job_request_details": description or f"Attend breakdown on {machine or 'machine'}",
        "date_raised": raised,
        "requested_by": reporter,
        "section": breakdown.get("section"),
        # The job is raised before the breakdown is stored (so a failed raise
        # stores nothing), so the note cites the machine and date, not an id.
        "notes": f"Raised from the breakdown on {machine or 'machine'}, {raised}.",
    }


def breakdown_shell_for_work_order(work_order: dict) -> dict:
    """The breakdown shell a breakdown work order raises to record its downtime.

    Times and metrics stay NULL (unknown, never zero); the artisan records
    them on the breakdown. Pure: no database reads.
    """
    machine = (work_order.get("equipment_info") or work_order.get("equipment") or "").strip()
    artisan = (work_order.get("allocated_to") or work_order.get("artisan_name") or "").strip()
    wo_date = (work_order.get("date_raised") or "")[:10] or today_harare().isoformat()
    wo_priority = (work_order.get("priority") or "").strip().lower()
    priority = "critical" if wo_priority == "urgent" else (wo_priority if wo_priority in ("high", "medium", "low") else "medium")
    return {
        "machine_id": machine,
        "machine_name": machine,
        "artisan_name": artisan,
        "department": (work_order.get("to_department") or work_order.get("department") or "").strip() or "Engineering",
        "section": work_order.get("section"),
        "location": "",
        "breakdown_date": wo_date,
        "breakdown_type": "other",
        "breakdown_description": (work_order.get("job_request_details") or "").strip(),
        "machine_description": (work_order.get("job_request_details") or "").strip(),
        "work_done": "",
        "artisan_recommendations": "",
        "breakdown_start": None,
        "breakdown_end": None,
        "work_start": None,
        "work_end": None,
        "spares_used": "[]",
        "total_spare_cost": 0.0,
        "response_time_minutes": None,
        "repair_time_minutes": None,
        "downtime_minutes": None,
        "net_downtime_minutes": None,
        "status": "logged",
        "priority": priority,
    }
