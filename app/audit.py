# backend/app/audit.py — who changed what, and when, recorded in one place so no handler can forget it.
#
# Every write to the payroll and people tables (AUDITED_TABLES) passes through the shared Supabase client, which is
# wrapped here. After a write succeeds, one row per affected record is added to `audit_log`: the signed-in person
# (taken from the verified session, never from anything the browser sent), the action, the table, the record id, the
# fields that changed (updates) or the whole row (creates and deletes, so a deleted record can always be put back).
#
#   * A new write endpoint on a guarded table is audited automatically. Nothing to remember, nothing to forget.
#   * The audit write can never block or undo the person's action: if it fails it is logged loudly and counted, and
#     /api/health reports `audit: failing` (or `missing` before the table has been created), so a gap cannot be silent.
#   * Changes made directly in the Supabase SQL editor are not seen here; this records what the application did.
#
# The audited data is the application's own: the approval signature image is never copied into the log, and long text
# is shortened, so the log stays small and holds nothing it should not.
from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# The tables where a change to a row is a change to someone's pay, leave, equipment or record.
AUDITED_TABLES = frozenset({
    "overtime", "leaves", "timesheets", "artisan_timesheets", "employees", "ppe_records",
    "stock_issues", "requisitions", "requisition_items", "drivers", "training_certifications",
})

_SKIP_FIELDS = frozenset({"approval_signature", "signature", "password", "token"})
_MAX_TEXT = 500

_ACTOR: ContextVar[Optional[Dict[str, Any]]] = ContextVar("audit_actor", default=None)


def set_actor(user: Optional[Dict[str, Any]]) -> None:
    """Remember who is making this request (called by the sign-in check, from the verified session)."""
    _ACTOR.set(user)


def current_actor() -> Optional[Dict[str, Any]]:
    return _ACTOR.get()


class AuditState:
    """What /api/health reports about the audit trail."""
    written = 0
    failures = 0
    table_missing = False
    last_error: Optional[str] = None


def audit_status() -> Dict[str, Any]:
    if AuditState.table_missing:
        return {"status": "missing", "detail": "audit_log table not created yet", "written": AuditState.written}
    if AuditState.failures:
        return {"status": "failing", "failures": AuditState.failures, "written": AuditState.written}
    return {"status": "ok", "written": AuditState.written}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in _SKIP_FIELDS}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str) and len(value) > _MAX_TEXT:
        return f"{value[:_MAX_TEXT]}… [{len(value) - _MAX_TEXT} more characters not kept]"
    return value


def actor_name() -> str:
    """The signed-in person's name for a record they create or sign, from the verified session ('system' outside a request)."""
    actor = current_actor() or {}
    return str(actor.get("name") or actor.get("email") or "system")


def decision_stamp(status: Any, actor: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The approver/rejecter fields for a status change, from the session. Empty for any other status.

    Approving or rejecting is a signed act: who did it and when are decided here, on the server, so they cannot be
    typed over or left out by the browser.
    """
    who = (actor or {}).get("name") or (actor or {}).get("email") or "system"
    now = datetime.now(timezone.utc).isoformat()
    if status == "approved":
        return {"approved_by": who, "approved_at": now}
    if status == "rejected":
        return {"rejected_by": who, "rejected_at": now}
    return {}


def _action(op: str, payload: Any) -> str:
    if op == "insert":
        return "create"
    if op == "delete":
        return "delete"
    if op == "upsert":
        return "upsert"
    status = str(payload.get("status")) if isinstance(payload, dict) else ""
    return {"approved": "approve", "rejected": "reject", "cancelled": "cancel"}.get(status, "update")


def _record(real: Any, op: str, table: str, payload: Any, response: Any) -> None:
    data = getattr(response, "data", None)
    if not isinstance(data, list) or not data:
        return
    actor = current_actor() or {}
    action = _action(op, payload)
    entries: List[Dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        entry: Dict[str, Any] = {
            "actor_id": actor.get("user_id"),
            "actor_email": actor.get("email") or "system",
            "actor_name": actor.get("name"),
            "actor_role": actor.get("role"),
            "action": action,
            "table_name": table,
            "record_id": str(row.get("id")) if row.get("id") is not None else None,
        }
        if op == "update":
            entry["changes"] = _clean(payload if isinstance(payload, dict) else {})
        else:
            entry["snapshot"] = _clean(row)
        entries.append(entry)
    if not entries:
        return
    try:
        real.table("audit_log").insert(entries).execute()
        AuditState.written += len(entries)
    except Exception as exc:  # the person's action already succeeded; never undo or block it for the log
        text = str(exc)
        if "audit_log" in text and ("does not exist" in text or "PGRST205" in text or "42P01" in text):
            if not AuditState.table_missing:
                logger.error("audit_log table does not exist: changes are NOT being recorded. Run supabase_migration_integrity_constraints.sql")
            AuditState.table_missing = True
        else:
            AuditState.failures += 1
            AuditState.last_error = text[:300]
            logger.error(f"audit write failed for {table} {action}: {exc}")


class _AuditedQuery:
    """A write that is recorded once it has succeeded. Every other builder call is passed straight through."""

    def __init__(self, query: Any, real: Any, table: str, op: str, payload: Any) -> None:
        self._query = query
        self._real = real
        self._table = table
        self._op = op
        self._payload = payload

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._query, name)
        if not callable(attribute):
            return attribute

        def chained(*args: Any, **kwargs: Any) -> Any:
            result = attribute(*args, **kwargs)
            if result is not None and result is not self._query:
                self._query = result
            return self

        return chained

    def execute(self) -> Any:
        response = self._query.execute()
        try:
            _record(self._real, self._op, self._table, self._payload, response)
        except Exception as exc:  # recording must never break the write that already happened
            AuditState.failures += 1
            AuditState.last_error = str(exc)[:300]
            logger.error(f"audit recording failed for {self._table}: {exc}")
        return response


class _AuditedTable:
    def __init__(self, real: Any, table: str) -> None:
        self._real = real
        self._table = table
        self._builder = real.table(table)

    def insert(self, rows: Any, **kwargs: Any) -> _AuditedQuery:
        return _AuditedQuery(self._builder.insert(rows, **kwargs), self._real, self._table, "insert", rows)

    def upsert(self, rows: Any, **kwargs: Any) -> _AuditedQuery:
        return _AuditedQuery(self._builder.upsert(rows, **kwargs), self._real, self._table, "upsert", rows)

    def update(self, values: Any, **kwargs: Any) -> _AuditedQuery:
        return _AuditedQuery(self._builder.update(values, **kwargs), self._real, self._table, "update", values)

    def delete(self, **kwargs: Any) -> _AuditedQuery:
        return _AuditedQuery(self._builder.delete(**kwargs), self._real, self._table, "delete", None)

    def __getattr__(self, name: str) -> Any:  # select and anything else reads: not audited
        return getattr(self._builder, name)


class AuditedClient:
    """The shared Supabase client, with writes to AUDITED_TABLES recorded. Everything else is the real client."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def table(self, name: str) -> Any:
        if name in AUDITED_TABLES:
            return _AuditedTable(self._real, name)
        return self._real.table(name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


_COLUMN_PROBE: Dict[Any, tuple] = {}
_ABSENT_RECHECK_SECONDS = 60


def has_columns(client: Any, table: str, columns: Iterable[str]) -> bool:
    """Whether `table` has all of `columns` right now. Lets code that stamps a new column work before and after the
    migration that adds it, instead of failing every request until the SQL has been run.

    A yes is remembered for the life of the process. A no is re-checked after a minute, so running the migration takes
    effect without a restart. A failure that is not "the column is missing" (a network blip) answers no without
    remembering it.
    """
    import time

    wanted = tuple(columns)
    key = (table, wanted)
    found = _COLUMN_PROBE.get(key)
    if found is not None and (found[0] or time.monotonic() - found[1] < _ABSENT_RECHECK_SECONDS):
        return found[0]
    try:
        client.table(table).select(",".join(wanted)).limit(1).execute()
        _COLUMN_PROBE[key] = (True, time.monotonic())
        return True
    except Exception as exc:
        text = str(exc)
        if "42703" in text or "PGRST204" in text or "does not exist" in text or "Could not find" in text:
            _COLUMN_PROBE[key] = (False, time.monotonic())
        return False
