# app/serialization.py — shared helpers for shaping Supabase records into
# JSON-serializable dicts. Was copy-pasted byte-for-byte into maintenance.py,
# ppe.py, and spares.py; consolidated here so a fix only has to be made once.

import json
from datetime import date, datetime


def convert_dates_to_iso(record: dict) -> dict:
    """Convert date/datetime values in a record to ISO format strings for JSON serialization."""
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(value, (date, datetime)):
                record[key] = value.isoformat()
    return record


# encode_json_fields/decode_json_fields: the "store a list as a JSON string column,
# parse it back on read" pattern was hand-rolled with its own try/except per field in
# timesheets.py (overtime_periods), overtime.py (spares_used), breakdowns.py
# (spares_used), and daily_reports.py (call_outs, equipment) — same shape every time,
# consolidated here. NOTE: whether these columns are actually TEXT (needing this) or
# JSONB (which Supabase/PostgREST serializes natively, no manual json.dumps needed —
# see maintenance.py's prepare_data_for_db and its own comment on this) hasn't been
# verified against the live schema. This preserves each call site's existing,
# proven-in-production behavior unchanged; it does not attempt to remove the
# encode/decode step itself, which would be a schema-verification task, not a
# duplication-cleanup one.

def encode_json_fields(data: dict, fields: list) -> dict:
    """Stringify each of `fields` (defaulting missing/falsy values to an empty list)
    for storage in a JSON-string column, mirroring what every call site already did by
    hand. Returns a new dict; `data` itself is left untouched."""
    result = dict(data)
    for f in fields:
        result[f] = json.dumps(result.get(f) or [])
    return result


def decode_json_fields(record: dict, fields: list) -> dict:
    """Reverse of encode_json_fields — parse each of `fields` back from its stored JSON
    string. Falls back to [] on a malformed/legacy value rather than raising, same as
    every existing call site's own try/except. Returns a new dict; `record` itself is
    left untouched. Fields that are already lists/dicts (not strings) are left as-is —
    handles a JSONB column that never needed stringifying in the first place."""
    result = dict(record)
    for f in fields:
        if isinstance(result.get(f), str):
            try:
                result[f] = json.loads(result[f])
            except (TypeError, ValueError, json.JSONDecodeError):
                result[f] = []
    return result
