# backend/app/ppe_conflicts.py — the same item is not issued to the same person on the same day twice.
#
# PPE was recorded with no duplicate protection at all, so the same issue was entered again (a second laptop, a repeat
# click on a slow page) and the register then showed the person twice. The identity of an issue is the person, the kind
# of PPE and the issue date. Items already returned, replaced, lost or disposed of no longer count as issued.
from __future__ import annotations

from typing import Any, Iterable, Optional

CLOSED_STATUSES = frozenset({"returned", "replaced", "lost", "disposed", "cancelled"})


def _same(a: object, b: object) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()


def find_duplicate(candidates: Iterable[dict], ppe_type: object, issue_date: object, ignore_id: object = None) -> Optional[dict]:
    """The first record in `candidates` (one person's PPE) that is the same kind issued on the same day, or None."""
    if not ppe_type or not issue_date:
        return None
    for row in candidates:
        if ignore_id is not None and str(row.get("id")) == str(ignore_id):
            continue
        if str(row.get("status") or "active").lower() in CLOSED_STATUSES:
            continue
        if _same(row.get("ppe_type"), ppe_type) and str(row.get("issue_date") or "")[:10] == str(issue_date)[:10]:
            return row
    return None


def existing_duplicate(client: Any, employee_id: Optional[str], ppe_type: object, issue_date: object, ignore_id: object = None) -> Optional[dict]:
    """Look up the person's PPE issued on that day and return the duplicate, if any. `client` is the router's Supabase client."""
    if not employee_id or not ppe_type or not issue_date:
        return None
    response = (
        client.table("ppe_records")
        .select("id,employee_name,ppe_type,item_name,issue_date,status")
        .eq("employee_id", employee_id)
        .eq("issue_date", str(issue_date)[:10])
        .execute()
    )
    return find_duplicate(response.data or [], ppe_type, issue_date, ignore_id)


def duplicate_message(row: dict) -> str:
    who = str(row.get("employee_name") or "This person").strip()
    return (
        f"{who} already has {row.get('ppe_type')} recorded as issued on {str(row.get('issue_date'))[:10]} "
        f"(record #{row.get('id')}). Edit that record instead of adding another."
    )
