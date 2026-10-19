# backend/app/concurrency.py — two people editing the same record must not silently overwrite each other.
#
# Without this, the second save wins and the first person's change vanishes without a word. The edit form sends back
# the `updated_at` it was opened with; if the record has changed since, the save is refused with a plain 409 and the
# person reloads. The check is optional and tolerant on purpose: a caller that sends nothing (an older page, a script)
# is not blocked, and a table without an `updated_at` column yet (before its migration) is not checked.
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

CHANGED_MESSAGE = "Someone else changed this record after you opened it. Reload it to see their changes, then make yours again."


def _moment(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def assert_unchanged(existing: Dict[str, Any], expected_updated_at: Any) -> None:
    """Raise 409 when the record has been changed since the caller loaded it. A no-op when the caller sent no
    expectation or the record has no `updated_at`."""
    if expected_updated_at in (None, ""):
        return
    current = existing.get("updated_at")
    if current in (None, ""):
        return
    a, b = _moment(current), _moment(expected_updated_at)
    same = (a == b) if (a is not None and b is not None) else (str(current) == str(expected_updated_at))
    if not same:
        raise HTTPException(status_code=409, detail=CHANGED_MESSAGE)


def touch(existing: Dict[str, Any], data: Dict[str, Any]) -> None:
    """Move `updated_at` forward on a save, for tables that have the column (some rely on a database trigger; this
    makes it hold for all of them, so the check above always has something to compare)."""
    if "updated_at" in existing:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
