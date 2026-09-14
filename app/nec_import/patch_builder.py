"""Deterministic timesheet patches from interpreted scan rows (no payroll totals here)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.nec_import.employee_match import normalize_code

LEAVE_STATUSES = {"leave", "sick"}


def leave_on_date(leaves: List[dict], human_code: str, ds: str) -> Optional[dict]:
    hc = normalize_code(human_code) or human_code
    for lv in leaves:
        lv_code = normalize_code(lv.get("employee_id")) or lv.get("employee_id")
        if lv_code != hc:
            continue
        if lv.get("status") == "rejected":
            continue
        if lv.get("start_date") <= ds <= lv.get("end_date"):
            return lv
    return None


def map_work_status(interp_status: Optional[str]) -> Optional[str]:
    if interp_status == "off":
        return "off"
    if interp_status == "work":
        return "work"
    if interp_status in LEAVE_STATUSES:
        return None
    return None


def build_patch(
    row: dict,
    existing: Optional[dict],
    human_code: str,
    leaves: List[dict],
) -> Tuple[Optional[dict], str]:
    interp = row.get("interpreted") or {}
    ds = row["date"]
    normal = interp.get("normal_hours_expected")
    if normal is None:
        return None, "skip_unresolved_normal"

    lv = leave_on_date(leaves, human_code, ds)
    if lv or interp.get("status") in LEAVE_STATUSES:
        if not lv:
            return None, "skip_leave_expected_no_module_record"
        night_h = interp.get("night_allowance_hours")
        standby = bool(interp.get("standby_marked"))
        if existing is None and night_h is None and not standby:
            return None, "skip_module_leave_day"
        patch: Dict[str, Any] = {}
        if night_h is not None and night_h > 0:
            patch["nightshift_hours"] = float(night_h)
            patch["nightshift_allowance"] = True
        if interp.get("standby_marked") is True:
            patch["standby_allowance"] = True
        if not patch:
            return None, "skip_module_leave_no_extras"
        return patch, "patch_leave_day_extras"

    status = map_work_status(interp.get("status"))
    if status is None:
        return None, "skip_unmapped_status"

    reg = float(normal) if status != "off" else 0.0
    night_h = interp.get("night_allowance_hours")
    patch: Dict[str, Any] = {
        "status": status,
        "regular_hours": reg,
    }
    if not existing:
        patch["overtime_hours"] = 0
        patch["holiday_overtime_hours"] = 0
    if night_h is not None and night_h > 0:
        patch["nightshift_hours"] = float(night_h)
        patch["nightshift_allowance"] = True
    elif existing and existing.get("nightshift_allowance"):
        pass
    if interp.get("standby_marked") is True:
        patch["standby_allowance"] = True

    nh = float(patch.get("nightshift_hours") or (existing or {}).get("nightshift_hours") or 0)
    patch["total_hours"] = reg + nh

    if existing:
        def _eq(field: str, new_val: Any) -> bool:
            old = existing.get(field)
            if isinstance(new_val, float) and isinstance(old, (int, float)):
                return abs(float(old) - new_val) < 1e-6
            return old == new_val

        if all(_eq(k, v) for k, v in patch.items()):
            return None, "skip_unchanged"
    return patch, "upsert"
