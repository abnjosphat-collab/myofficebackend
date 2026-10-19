# backend/app/errors.py — one honest shape for every failure a handler turns into a response.
#
# Handlers used to do `raise HTTPException(500, detail=f"Error doing X: {e}")`,
# which leaks the raw internal error string (table names, constraint text, driver
# messages) to whoever called the API. server_error() is the replacement: the
# full traceback goes to the server log with a short reference id, and the
# client gets a generic message carrying only that id — enough to correlate a
# user report with the log line, nothing more.
#
# Two things it also does, so that no handler has to remember them:
#   * An HTTPException that was raised inside a try block (a 404 from get_or_404, a 409 from a rule check) and
#     caught by a broad `except Exception` is handed back unchanged, so a missing record stays a 404 instead of
#     turning into a 500.
#   * A Postgres constraint violation (unique, foreign key, check, not-null) is the database enforcing a rule, not a
#     server fault, so it becomes the matching 409/422 with a plain sentence and never leaks the constraint name or
#     the offending values. The database is the last word on integrity; this keeps its refusals readable.
import logging
import re
import traceback
import uuid
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Sentences for constraints the product relies on, keyed by (part of) the constraint or index name. Anything not
# listed gets the generic sentence for its kind.
_KNOWN_CONSTRAINTS = {
    "overtime_one_live_request_per_slot": "This person already has an overtime request for exactly this time. Edit that request instead.",
    "leaves_one_live_request_per_range": "This person already has a leave request for exactly these dates. Edit that request instead.",
    "ppe_one_record_per_issue": "This item was already recorded as issued to this person on this date.",
    "work_order_number": "That work order number is already in use.",
    "documents_one_copy_per_folder": "This exact file is already in that folder.",
    "document_folders_one_name_per_parent": "A folder with this name already exists here.",
    "document_folders_seed_key_idx": "That folder of the AMS structure already exists.",
}


def _constraint_name(message: str) -> Optional[str]:
    found = re.search(r'constraint "([^"]+)"', message or "")
    if found:
        return found.group(1)
    found = re.search(r'unique index "([^"]+)"', message or "")
    return found.group(1) if found else None


def _database_refusal(exc: BaseException) -> Optional[HTTPException]:
    """The 409/422 a Postgres constraint violation deserves, or None when `exc` is not one."""
    code = getattr(exc, "code", None)
    if not isinstance(code, str):
        return None
    message = str(getattr(exc, "message", "") or "")
    details = str(getattr(exc, "details", "") or "")
    name = _constraint_name(message) or ""
    known = next((text for key, text in _KNOWN_CONSTRAINTS.items() if key in name), None)

    if code == "23505":  # unique violation
        return HTTPException(status_code=409, detail=known or "That record already exists: it would duplicate one that is already saved.")
    if code == "23503":  # foreign-key violation
        if "is still referenced" in details:
            return HTTPException(status_code=409, detail=known or "Other records still use it, so it cannot be removed. Cancel or archive it instead.")
        return HTTPException(status_code=422, detail=known or "It refers to a record that does not exist. Pick it from the list instead of typing it.")
    if code == "23514":  # check violation
        return HTTPException(status_code=422, detail=known or "One of the values is outside what is allowed.")
    if code == "23502":  # not-null violation
        return HTTPException(status_code=422, detail=known or "A required value is missing.")
    return None


def server_error(exc: BaseException, what: str, status_code: int = 500) -> HTTPException:
    """Turn a caught exception into the response the client should get.

    Args:
        exc: The caught exception. An HTTPException is returned as it is; a database constraint violation becomes a
            409/422; anything else is logged with a traceback and answered with a generic 500 carrying a reference.
        what: Short gerund phrase naming the failed operation, e.g.
            "Creating overtime" or "Fetching breakdowns".
        status_code: 500 by default; 502/503 when the fault is an upstream service or a missing dependency.
    """
    if isinstance(exc, HTTPException):
        return exc
    refusal = _database_refusal(exc)
    if refusal is not None:
        logger.warning(f"{what}: the database refused it ({getattr(exc, 'code', '?')}): {exc}")
        return refusal
    ref = uuid.uuid4().hex[:8]
    logger.error(f"{what} failed (ref {ref}): {exc}")
    logger.error(traceback.format_exc())
    return HTTPException(
        status_code=status_code,
        detail=f"{what} failed. Please try again. (ref {ref})",
    )
