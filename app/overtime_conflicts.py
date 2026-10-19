# backend/app/overtime_conflicts.py — one person cannot have two live overtime requests that cover the same time.
#
# The register used to warn about a repeat only in the browser, using whatever list that laptop had loaded, and never
# blocked it: the server and the database accepted any number of identical rows, so a second laptop (or a stale page)
# produced duplicates that were then approved and counted twice. The check lives here, on the server, where every
# writer meets it. It is pure (rows in, a row or None out) so it is tested without a database.
#
# What counts as the same time: the two windows overlap. Identical windows overlap; so do 17:00-19:00 and 18:00-20:00.
# Back-to-back windows (17:00-19:00 then 19:00-21:00) do not, and neither do separate shifts on the same day (a call-out
# after a normal shift), which are real. A window that ends at or before it starts crosses midnight (22:00-06:00), and
# a night shift on the evening before counts against the early hours of the next date.
# Requests entered as hours only (no start and end times) carry no time window, so they cannot be compared.
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

# A request in either of these states no longer claims the time.
INACTIVE_STATUSES = frozenset({"rejected", "cancelled"})

_MINUTES_PER_DAY = 24 * 60


def _clock_minutes(value: object) -> Optional[int]:
    """'HH:MM' (or 'H:MM', optionally with seconds) as minutes after midnight, or None when it is not a clock time."""
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.strip().split(":")
    try:
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None
    if not (0 <= hours <= 24 and 0 <= minutes < 60):
        return None
    return hours * 60 + minutes


def time_window(day: object, start: object, end: object) -> Optional[tuple[int, int]]:
    """The request's window as (start, end) in minutes on one continuous timeline, or None when it has no usable times."""
    try:
        ordinal = date.fromisoformat(str(day)[:10]).toordinal()
    except ValueError:
        return None
    begin, finish = _clock_minutes(start), _clock_minutes(end)
    if begin is None or finish is None:
        return None
    if finish <= begin:  # ends at or before it starts: it runs past midnight
        finish += _MINUTES_PER_DAY
    base = ordinal * _MINUTES_PER_DAY
    return base + begin, base + finish


def neighbour_dates(day: object) -> list[str]:
    """The date, the day before and the day after: the only dates whose requests can overlap one on `day`."""
    try:
        d = date.fromisoformat(str(day)[:10])
    except ValueError:
        return []
    return [(d + timedelta(days=offset)).isoformat() for offset in (-1, 0, 1)]


def find_conflict(
    candidates: Iterable[dict],
    day: object,
    start: object,
    end: object,
    ignore_id: object = None,
) -> Optional[dict]:
    """The first live request in `candidates` whose time overlaps the given window, or None.

    `candidates` are one person's requests (the caller filters by person and nearby dates). `ignore_id` is the request
    being edited, which must not conflict with itself.
    """
    mine = time_window(day, start, end)
    if mine is None:
        return None
    for row in candidates:
        if ignore_id is not None and str(row.get("id")) == str(ignore_id):
            continue
        if str(row.get("status") or "pending").lower() in INACTIVE_STATUSES:
            continue
        theirs = time_window(row.get("date"), row.get("start_time"), row.get("end_time"))
        if theirs is not None and mine[0] < theirs[1] and theirs[0] < mine[1]:
            return row
    return None


def conflict_message(row: dict) -> str:
    """The sentence the person sees, naming the request that is in the way."""
    who = str(row.get("employee_name") or "This person").strip()
    status = str(row.get("status") or "pending").lower()
    return (
        f"{who} already has overtime on {row.get('date')} from {row.get('start_time')} to {row.get('end_time')} "
        f"({status}, request #{row.get('id')}). Two requests cannot cover the same time: change the times, "
        f"or edit that request instead."
    )
