# app/serialization.py — shared helpers for shaping Supabase records into
# JSON-serializable dicts. Was copy-pasted byte-for-byte into maintenance.py,
# ppe.py, and spares.py; consolidated here so a fix only has to be made once.

from datetime import date, datetime


def convert_dates_to_iso(record: dict) -> dict:
    """Convert date/datetime values in a record to ISO format strings for JSON serialization."""
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(value, (date, datetime)):
                record[key] = value.isoformat()
    return record
