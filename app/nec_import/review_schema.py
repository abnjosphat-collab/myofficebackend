"""Validate review JSON shape (not payroll totals)."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, List, Tuple

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_SHEETS = 500


def _parse_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def validate_review_payload(data: Any) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["root must be an object"]
    if data.get("automatic_writes_allowed") is True:
        errors.append("automatic_writes_allowed must not be true for import API")
    period = data.get("period") or {}
    if not period.get("start_date") or not period.get("end_date"):
        errors.append("period.start_date and period.end_date required")
    elif not _parse_iso_date(period.get("start_date")) or not _parse_iso_date(period.get("end_date")):
        errors.append("period.start_date and period.end_date must be YYYY-MM-DD")
    sheets = data.get("sheets")
    if not isinstance(sheets, list):
        errors.append("sheets must be an array")
        return len(errors) == 0, errors
    if len(sheets) > _MAX_SHEETS:
        errors.append(f"sheets exceeds maximum of {_MAX_SHEETS}")
    for i, sh in enumerate(sheets):
        if not isinstance(sh, dict):
            errors.append(f"sheets[{i}] must be object")
            continue
        rows = sh.get("rows")
        if rows is not None and not isinstance(rows, list):
            errors.append(f"sheets[{i}].rows must be array")
            continue
        if not isinstance(rows, list):
            continue
        for j, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f"sheets[{i}].rows[{j}] must be object")
                continue
            ds = row.get("date")
            if ds is not None and not _parse_iso_date(ds):
                errors.append(f"sheets[{i}].rows[{j}].date invalid")
            interp = row.get("interpreted") or {}
            if isinstance(interp, dict):
                for key in ("normal_hours_expected", "night_allowance_hours"):
                    v = interp.get(key)
                    if v is not None and isinstance(v, (int, float)) and v < 0:
                        errors.append(f"sheets[{i}].rows[{j}].interpreted.{key} must not be negative")
    return len(errors) == 0, errors
