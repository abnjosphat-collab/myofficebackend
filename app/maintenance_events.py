"""Append-only audit trail for the Maintenance module.

Every write to a work order (and, in later slices, requests, schedules and assignments) leaves one row
in ``maintenance_events``. The table is created by ``supabase_migration_maintenance_audit.sql`` and the
database refuses any UPDATE or DELETE on it.

An audit row is written *after* the change it describes. If the append fails, the change stands (the
existing endpoints must keep working) and the failure is logged at ERROR with the entity so the gap can
be found; it is never reported as success of the audit.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

EVENTS_TABLE = "maintenance_events"

# Columns that are bookkeeping, not a user's edit. They never appear in a change list.
_IGNORED_FIELDS = frozenset({"updated_at", "created_at", "version", "id"})

# Columns holding a signature image (a data URL). The audit row records that they changed, not the
# image itself, so the trail stays small; signed approvals keep their image in ``signature``.
_REDACTED_SUFFIXES = ("_sign", "_signature")

# A long free-text value is shortened in the change list; the full text is on the record itself.
_MAX_VALUE_CHARS = 500


def actor_of(user: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Return the audit fields that identify the signed-in user.

    Args:
        user: The dict returned by ``get_current_user``.

    Returns:
        ``actor_user_id`` and ``actor_name`` (the sign-in email, as nothing richer is held on the token).
    """
    return {"actor_user_id": user.get("user_id"), "actor_name": user.get("email") or None}


def _shown(field: str, value: Any) -> Any:
    if field.endswith(_REDACTED_SUFFIXES):
        return "(signature)" if value else None
    if isinstance(value, str) and len(value) > _MAX_VALUE_CHARS:
        return value[:_MAX_VALUE_CHARS] + "…"
    return value


def diff_changes(before: Dict[str, Any], after: Dict[str, Any], fields: Optional[Iterable[str]] = None) -> Dict[str, List[Any]]:
    """Describe what changed between two versions of a row.

    Args:
        before: The row before the write.
        after: The row after the write.
        fields: Limit the comparison to these columns; every column in ``after`` when omitted.

    Returns:
        ``{column: [old, new]}`` for each column whose value differs. Bookkeeping columns are skipped
        and signature images are replaced by a marker.
    """
    changes: Dict[str, List[Any]] = {}
    for field in (fields if fields is not None else after.keys()):
        if field in _IGNORED_FIELDS:
            continue
        old, new = before.get(field), after.get(field)
        if old != new:
            changes[field] = [_shown(field, old), _shown(field, new)]
    return changes


def append_event(
    db: Any,
    *,
    entity: str,
    entity_id: int,
    action: str,
    user: Dict[str, Any],
    entity_number: Optional[str] = None,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    changes: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
    signature: Optional[str] = None,
) -> bool:
    """Append one audit row. Never raises.

    Args:
        db: Supabase-compatible client.
        entity: ``work_order``, ``request``, ``schedule`` or ``assignment``.
        entity_id: The primary key of the record the event is about.
        action: ``created``, ``updated``, ``deleted``, ``commented``, ``transition``, and so on.
        user: The dict returned by ``get_current_user``.
        entity_number: The readable number (for example ``WO-00012``), kept so the trail outlives the row.
        from_status: Status before, for a transition.
        to_status: Status after, for a transition.
        changes: ``{column: [old, new]}`` from :func:`diff_changes`.
        note: A reason or comment.
        signature: A signature data URL, kept only on signed approvals and sign-offs.

    Returns:
        True when the row was stored, False when the append failed (the failure is logged).
    """
    row = {
        "entity": entity,
        "entity_id": entity_id,
        "entity_number": entity_number,
        "action": action,
        "from_status": from_status,
        "to_status": to_status,
        "changes": changes or {},
        "note": note,
        "signature": signature,
        **actor_of(user),
    }
    try:
        db.table(EVENTS_TABLE).insert(row).execute()
        return True
    except Exception as err:  # noqa: BLE001 - the audit must never break the write it describes
        logger.error("Audit append failed for %s %s (%s): %s", entity, entity_id, action, err)
        return False
