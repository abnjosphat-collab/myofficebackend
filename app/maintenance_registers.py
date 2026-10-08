"""Register reads and the leave rule for the Maintenance module.

Maintenance never keeps its own copy of people or tools. The work order form picks from the employees
register, the leave register and the Tools & Equipment register, and this module is where those reads
are shaped for it:

* :func:`people_on_leave` lists who is on approved leave on a day, so the form can grey them out.
* :func:`refuse_people_on_leave` is the server-side rule behind that greying: a name that is typed in
  by hand is refused just the same.
* :func:`tools_feed` is the read-only bridge to the Tools register, which has its own sign-in and so
  cannot be called from a maintenance page directly. It writes nothing and never changes custody.

A read that fails is raised as a 5xx. It is never turned into an empty list, because an empty list
here would read as "nobody is on leave" and "no tools exist".
"""

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException

from app.db_helpers import fetch_all_pages
from app.supabase_client import rows

logger = logging.getLogger(__name__)

# The work order columns that put a person on the job. The requester is not one of them: asking for
# work while on leave is allowed, doing it is not.
ASSIGNMENT_FIELDS = ("allocated_to", "responsible_foreman", "authorising_foreman", "artisan_name", "foreman_name")

LEAVES_TABLE = "leaves"
TOOLS_TABLE = "tools_workspace_equipment"
INSPECTIONS_TABLE = "tools_workspace_inspections"


def _unavailable(what: str, err: Exception) -> HTTPException:
    logger.error("Maintenance register read failed (%s): %s", what, err)
    return HTTPException(status_code=503, detail=f"The {what} could not be read. Try again in a moment.")


def _key(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


def _day(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def people_on_leave(db: Any, on: Optional[date] = None) -> List[Dict[str, Any]]:
    """Return everyone on approved leave on a day.

    Args:
        db: Supabase-compatible client.
        on: The day to check; today when omitted.

    Returns:
        One dict per approved leave covering the day: ``employee_id``, ``employee_name``,
        ``leave_type``, ``start_date`` and ``end_date`` (ISO dates).

    Raises:
        HTTPException: 503 when the leave register cannot be read.
    """
    day = on or datetime.now(timezone.utc).date()
    try:
        query = db.table(LEAVES_TABLE).select("*").eq("status", "approved")
        found = fetch_all_pages(lambda start, end: query.range(start, end).execute(), extract_rows=rows)
    except Exception as err:  # noqa: BLE001
        raise _unavailable("leave register", err) from err
    covering = []
    for leave in found:
        start, end = _day(leave.get("start_date")), _day(leave.get("end_date"))
        if start and end and start <= day <= end:
            covering.append({
                "employee_id": leave.get("employee_id"),
                "employee_name": leave.get("employee_name"),
                "leave_type": leave.get("leave_type"),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            })
    return sorted(covering, key=lambda item: _key(item["employee_name"]))


def refuse_people_on_leave(db: Any, values: Dict[str, Any], fields: Iterable[str] = ASSIGNMENT_FIELDS,
                           on: Optional[date] = None) -> None:
    """Refuse a work order that puts someone on approved leave on the job.

    Only the given values are looked at, so a caller passes the fields being set or changed and an old
    record can still be saved without touching them.

    Args:
        db: Supabase-compatible client.
        values: Column values being written.
        fields: The columns that name a person doing the work.
        on: The day to check; today when omitted.

    Raises:
        HTTPException: 409 with ``code`` ``person_on_leave`` and the people and dates, or 503 when the
            leave register cannot be read.
    """
    named = {field: values.get(field) for field in fields if str(values.get(field) or "").strip()}
    if not named:
        return
    away = {_key(item["employee_name"]): item for item in people_on_leave(db, on)}
    blocked = [
        {"field": field, "name": str(name).strip(), "leave_type": away[_key(name)]["leave_type"],
         "start_date": away[_key(name)]["start_date"], "end_date": away[_key(name)]["end_date"]}
        for field, name in named.items() if _key(name) in away
    ]
    if blocked:
        first = blocked[0]
        raise HTTPException(status_code=409, detail={
            "code": "person_on_leave",
            "message": f"{first['name']} is on {first['leave_type'] or 'leave'} until {first['end_date']}.",
            "people": blocked,
        })


def tools_feed(db: Any) -> List[Dict[str, Any]]:
    """Read the Tools & Equipment register for the maintenance tool picker.

    The overdue state and the checks that are due are derived exactly as the Tools workspace derives
    them. Archived tools are left out.

    Args:
        db: Supabase-compatible client.

    Returns:
        One dict per tool with only what a picker needs.

    Raises:
        HTTPException: 503 when the register cannot be read.
    """
    # Imported here: the Tools workspace module builds its router and in-memory test store on import.
    from app.routers.tools_workspace import _effective_status, _inspection_due

    try:
        tools = fetch_all_pages(lambda s, e: db.table(TOOLS_TABLE).select("*").range(s, e).execute(), extract_rows=rows)
        inspections = fetch_all_pages(lambda s, e: db.table(INSPECTIONS_TABLE).select("*").range(s, e).execute(), extract_rows=rows)
    except Exception as err:  # noqa: BLE001
        raise _unavailable("Tools register", err) from err

    feed = []
    for tool in tools:
        if tool.get("archived"):
            continue
        custody = tool.get("custody") or {}
        due = _inspection_due(tool, inspections)
        feed.append({
            "id": tool.get("id"),
            "register_number": tool.get("register_number"),
            "name": tool.get("name"),
            "make_model": tool.get("make_model"),
            "category": tool.get("category"),
            "equipment_kind": tool.get("equipment_kind"),
            "department": tool.get("department"),
            "status": _effective_status(tool),
            "holder": custody.get("employee_name"),
            "expected_return_at": custody.get("expected_return_at"),
            "inspection_due": due,
            "condition": tool.get("condition"),
        })
    return sorted(feed, key=lambda item: (str(item["register_number"] or ""), str(item["name"] or "")))
