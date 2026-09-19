"""Which review sheets are eligible for preview/apply."""
from __future__ import annotations

from typing import Dict, Iterable, List

from app.nec_import.employee_match import sheet_human_code

# Sheets the user explicitly rejected or replaced must not write payroll data.
_INELIGIBLE_DISPOSITIONS = frozenset({"superseded", "rejected"})


def eligible_review_sheets(sheets: Iterable[dict]) -> List[dict]:
    return [s for s in sheets if (s.get("disposition") or "pending") not in _INELIGIBLE_DISPOSITIONS]


def duplicate_sheet_groups(sheets: Iterable[dict]) -> List[dict]:
    """Multiple eligible sheets for the same identity key — must be resolved before apply."""
    dup_groups: Dict[str, List[str]] = {}
    for s in sheets:
        code = sheet_human_code(s) or (s.get("employee") or {}).get("name_raw") or "unknown"
        dup_groups.setdefault(code, []).append(s.get("sheet_id") or "?")
    return [{"key": k, "sheet_ids": v} for k, v in dup_groups.items() if len(v) > 1]
