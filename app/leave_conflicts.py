# backend/app/leave_conflicts.py — one person cannot have two live leave requests that cover the same day.
#
# Same rule, same reason as app/overtime_conflicts.py: the leave form only warned about an overlap in the browser,
# from whatever list that laptop had loaded, and the server accepted anything, so overlapping and identical requests
# accumulated (and were approved). The check lives here, on the server, where every writer (the leave form, a status
# change, taking lieu leave from overtime) meets it. The decision is pure (rows in, a row or None out).
#
# Leave is whole days, so two requests overlap when they share at least one day: start <= other end and other start
# <= end, both ends inclusive. A request that is rejected or cancelled no longer claims its days.
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional

INACTIVE_STATUSES = frozenset({"rejected", "cancelled"})


def _day(value: object) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def find_conflict(candidates: Iterable[dict], start: object, end: object, ignore_id: object = None) -> Optional[dict]:
    """The first live request in `candidates` that shares a day with start..end, or None."""
    mine_start, mine_end = _day(start), _day(end)
    if mine_start is None or mine_end is None:
        return None
    if mine_end < mine_start:  # a malformed request has no days to claim; the model validation rejects it separately
        return None
    for row in candidates:
        if ignore_id is not None and str(row.get("id")) == str(ignore_id):
            continue
        if str(row.get("status") or "pending").lower() in INACTIVE_STATUSES:
            continue
        their_start, their_end = _day(row.get("start_date")), _day(row.get("end_date"))
        if their_start is None or their_end is None:
            continue
        if mine_start <= their_end and their_start <= mine_end:
            return row
    return None


def overlapping_leave(client: Any, employee_id: Optional[str], start: object, end: object, ignore_id: object = None) -> Optional[dict]:
    """Look up the person's leave that could overlap start..end and return the one in the way, if any.

    `client` is the router's Supabase client, passed in so the router stays the one place that owns it.
    """
    if not employee_id or _day(start) is None or _day(end) is None:
        return None
    response = (
        client.table("leaves")
        .select("id,employee_name,start_date,end_date,leave_type,status")
        .eq("employee_id", employee_id)
        .lte("start_date", str(_day(end)))
        .gte("end_date", str(_day(start)))
        .execute()
    )
    return find_conflict(response.data or [], start, end, ignore_id)


def conflict_message(row: dict) -> str:
    """The sentence the person sees, naming the leave that is in the way."""
    who = str(row.get("employee_name") or "This person").strip()
    kind = str(row.get("leave_type") or "leave").replace("_", " ")
    status = str(row.get("status") or "pending").lower()
    return (
        f"{who} already has {kind} leave from {row.get('start_date')} to {row.get('end_date')} "
        f"({status}, request #{row.get('id')}). Two leave requests cannot cover the same day: change the dates, "
        f"or edit that request instead."
    )
