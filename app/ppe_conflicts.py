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


def existing_duplicate(client: Any, employee_id: Optional[str], ppe_type: object, issue_date: object,
                       ignore_id: object = None, *, employee_db_id: Optional[int] = None) -> Optional[dict]:
    """Find a duplicate by stable Personnel ID and check legacy numbered issues."""
    if not (employee_id or employee_db_id) or not ppe_type or not issue_date:
        return None

    def issued_on(column: str, value: Any, legacy_only: bool = False) -> Optional[dict]:
        query = (client.table("ppe_records")
                 .select("id,employee_name,ppe_type,item_name,issue_date,status")
                 .eq(column, value).eq("issue_date", str(issue_date)[:10]))
        if legacy_only:
            query = query.is_("employee_db_id", "null")
        return find_duplicate(query.execute().data or [], ppe_type, issue_date, ignore_id)

    if employee_db_id is not None:
        linked = issued_on("employee_db_id", employee_db_id)
        if linked:
            return linked
    return issued_on("employee_id", employee_id, legacy_only=employee_db_id is not None) if employee_id else None


def duplicate_message(row: dict) -> str:
    who = str(row.get("employee_name") or "This person").strip()
    return (
        f"{who} already has {row.get('ppe_type')} recorded as issued on {str(row.get('issue_date'))[:10]} "
        f"(record #{row.get('id')}). Edit that record instead of adding another."
    )
