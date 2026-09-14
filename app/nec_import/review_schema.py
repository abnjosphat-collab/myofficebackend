"""Validate review JSON shape (not payroll totals)."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def validate_review_payload(data: Any) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["root must be an object"]
    if data.get("automatic_writes_allowed") is True:
        errors.append("automatic_writes_allowed must not be true for import API")
    period = data.get("period") or {}
    if not period.get("start_date") or not period.get("end_date"):
        errors.append("period.start_date and period.end_date required")
    sheets = data.get("sheets")
    if not isinstance(sheets, list):
        errors.append("sheets must be an array")
        return len(errors) == 0, errors
    for i, sh in enumerate(sheets[:500]):
        if not isinstance(sh, dict):
            errors.append(f"sheets[{i}] must be object")
            continue
        rows = sh.get("rows")
        if rows is not None and not isinstance(rows, list):
            errors.append(f"sheets[{i}].rows must be array")
    return len(errors) == 0, errors
