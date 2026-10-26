# backend/app/maintenance_links.py — M1: how work orders and schedules link to the Equipment register.
#
# A link is made in exactly two ways, and nothing is ever guessed:
#   1. a compressor-routine:NN marker, which names the register id directly
#      (written by ensure_weekly_routine_schedules from real register rows), or
#   2. an exact name match, trimmed on both sides (the register holds
#      'Compressor No 1 ' with a trailing space; the typed name never will).
# Anything else stays unlinked (NULL) for a person to resolve — the M1 SQL
# lists those rows in its closing report.
"""Link work orders and schedules to the Equipment register (M1)."""

import re
from typing import Any, Optional

from app.scope import apply_list_scope

#: The classification every schedule-generated work order carries. Matches the
#: frontend WOClassification 'planned_maintenance' and the values already live.
PLANNED_MAINTENANCE = "planned_maintenance"

#: Must stay identical to app.routers.compressors.ROUTINE_MARKER_PREFIX, which
#: owns the marker. Kept as a local copy (pinned by test_maintenance_links)
#: because importing the compressors router here would drag numpy/pandas into
#: every router that resolves a link.
ROUTINE_MARKER_PREFIX = "compressor-routine:"

_ROUTINE_MARKER_RE = re.compile(r"^compressor-routine:(\d+)$")


def parse_routine_marker(text: Any) -> Optional[int]:
    """The register id inside a ``compressor-routine:NN`` marker, else None.

    Args:
        text: The equipment_info text to inspect.

    Returns:
        The register id when the whole text is exactly a marker, else None.
    """
    if not isinstance(text, str):
        return None
    match = _ROUTINE_MARKER_RE.match(text.strip())
    return int(match.group(1)) if match else None


def resolve_equipment_id(client: Any, name: Any, user: Any = None) -> Optional[int]:
    """Link one machine name to its Equipment register row.

    A marker resolves to its id only when that id is still on the register; a
    plain name resolves only on an exact match (trimmed both sides). Anything
    else — a typo, a machine missing from the register, a blank — answers None
    so the row stays honestly unlinked instead of pointing at the wrong machine.

    Args:
        client: Supabase client to read the register through.
        name: The typed machine name (work order or schedule equipment_info).
        user: The viewer's session, so a section-scoped account only links
            machines it can see; None for the server-side generation job, which
            has no viewer and reads the whole register.

    Returns:
        The register id, or None when nothing matches exactly.
    """
    text = name.strip() if isinstance(name, str) else ""
    if not text:
        return None
    query = apply_list_scope(client.table("equipment").select("id,name"), user, "equipment")
    rows = getattr(query.execute(), "data", None) or []
    by_id = {}
    for row in rows:
        by_id[row.get("id")] = (row.get("name") or "").strip()
    marker_id = parse_routine_marker(text)
    if marker_id is not None:
        return marker_id if marker_id in by_id else None
    for row_id, row_name in by_id.items():
        if row_name == text:
            return row_id
    return None
