"""Work order lifecycle rules for the Maintenance module: who may move a job, to where, and with what.

The table lives here once. The API checks every move against it and returns the moves open to the
signed-in user, so the screen never keeps a copy of the rules.

Only the four statuses that already exist are used (``pending``, ``in-progress``, ``on-hold``,
``completed``); adding postponed, not-done or cancelled needs product approval and wiring through the
stats and filters. Foreman sign-off is a fact beside ``completed``, not a status.

The sign-in token carries an email and a role but not which employee the user is, so "the person doing
the job" cannot be told apart from any other ``user``. Moves open to the assignee are therefore open to
every role from ``user`` up; moves that need a manager need ``manager``. This is the same rule the old
status edit allowed, made explicit.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.auth import role_at_least

PENDING, IN_PROGRESS, ON_HOLD, COMPLETED = "pending", "in-progress", "on-hold", "completed"
STATUSES = (PENDING, IN_PROGRESS, ON_HOLD, COMPLETED)

# The permits a job can be flagged with. The keys are fixed; a label is editable text.
PERMIT_KEYS = ("permit_to_work", "hot_work", "hazardous_work", "confined_space", "high_voltage_switching", "land_disturbance", "other")
PERMIT_NAMES = {
    "permit_to_work": "Permit to work", "hot_work": "Hot work", "hazardous_work": "Hazardous work",
    "confined_space": "Confined space", "high_voltage_switching": "High-voltage switching",
    "land_disturbance": "Land disturbance and vegetation clearance", "other": "Other permit",
}


@dataclass(frozen=True)
class Move:
    """One allowed status change."""
    to: str
    min_role: str
    needs_reason: bool = False
    needs_signature: bool = False
    checks_permits: bool = False
    clears_signoff: bool = False


# (from status) -> moves
TRANSITIONS: Dict[str, List[Move]] = {
    PENDING: [
        Move(IN_PROGRESS, "user", checks_permits=True),
        Move(ON_HOLD, "manager", needs_reason=True),
    ],
    IN_PROGRESS: [
        Move(COMPLETED, "user", needs_signature=True),
        Move(ON_HOLD, "user", needs_reason=True),
    ],
    ON_HOLD: [
        Move(IN_PROGRESS, "user", checks_permits=True),
    ],
    COMPLETED: [
        Move(IN_PROGRESS, "manager", needs_reason=True, clears_signoff=True),
    ],
}


def find_move(from_status: Optional[str], to_status: str) -> Optional[Move]:
    """The rule for a move, or None when the table has no such move."""
    return next((m for m in TRANSITIONS.get(from_status or "", []) if m.to == to_status), None)


def allowed_moves(from_status: Optional[str], role: str) -> List[Move]:
    """The moves from a status that a user with this role may make."""
    return [m for m in TRANSITIONS.get(from_status or "", []) if role_at_least(role, m.min_role)]


def describe(move: Move) -> Dict[str, Any]:
    """A move as the API returns it."""
    return {
        "to": move.to, "needs_reason": move.needs_reason, "needs_signature": move.needs_signature,
        "checks_permits": move.checks_permits, "min_role": move.min_role,
    }


def normalise_permits(value: Any) -> Dict[str, Dict[str, Any]]:
    """Check and clean a permits object.

    Args:
        value: ``{key: {required, reference, label?}}`` or None.

    Returns:
        The same shape with every key known, booleans and trimmed text.

    Raises:
        ValueError: on an unknown key or a value that is not an object.
    """
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("permits must be an object")
    clean: Dict[str, Dict[str, Any]] = {}
    for key, item in value.items():
        if key not in PERMIT_KEYS:
            raise ValueError(f"unknown permit '{key}'")
        if not isinstance(item, dict):
            raise ValueError(f"permit '{key}' must be an object")
        entry: Dict[str, Any] = {
            "required": bool(item.get("required")),
            "reference": str(item.get("reference") or "").strip()[:120],
        }
        label = str(item.get("label") or "").strip()[:80]
        if label:
            entry["label"] = label
        clean[key] = entry
    return clean


def missing_permit_references(permits: Any) -> List[str]:
    """Names of the flagged permits that have no reference yet (these block starting the job)."""
    if not isinstance(permits, dict):
        return []
    out = []
    for key in PERMIT_KEYS:
        item = permits.get(key)
        if isinstance(item, dict) and item.get("required") and not str(item.get("reference") or "").strip():
            out.append(str(item.get("label") or "").strip() or PERMIT_NAMES[key])
    return out


def is_signature(value: Any) -> bool:
    """True for an image data URL, which is what the signature pad produces."""
    return isinstance(value, str) and value.startswith("data:image/") and len(value) > 40
