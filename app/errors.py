# backend/app/errors.py — one honest shape for every 500.
#
# Handlers used to do `raise HTTPException(500, detail=f"Error doing X: {e}")`,
# which leaks the raw internal error string (table names, constraint text, driver
# messages) to whoever called the API. server_error() is the replacement: the
# full traceback goes to the server log with a short reference id, and the
# client gets a generic message carrying only that id — enough to correlate a
# user report with the log line, nothing more.
#
# Deliberate 4xx messages (validation, not-found, permission) are NOT routed
# through here: those are safe by design and stay as they are.
import logging
import traceback
import uuid

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def server_error(exc: BaseException, what: str) -> HTTPException:
    """Log `exc` with a traceback server-side; return a generic 500 for the client.

    Args:
        exc: The caught exception (logged, never sent to the client).
        what: Short gerund phrase naming the failed operation, e.g.
            "Creating overtime" or "Fetching breakdowns".
    """
    ref = uuid.uuid4().hex[:8]
    logger.error(f"{what} failed (ref {ref}): {exc}")
    logger.error(traceback.format_exc())
    return HTTPException(
        status_code=500,
        detail=f"{what} failed. Please try again. (ref {ref})",
    )
