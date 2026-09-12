#!/usr/bin/env python3
"""Import NEC timesheet scan review JSON into MyOffice timesheets (Aug 13–Sep 12 2026).

Uses interpreted row values only; leaves/overtime come from existing modules on the grid.
Run from backend/:  .venv/Scripts/python.exe scripts/nec_timesheet_import.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# backend/ on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.supabase_client import supabase  # noqa: E402

PERIOD_START = "2026-08-13"
PERIOD_END = "2026-09-12"
DEFAULT_REVIEW = Path(
    r"C:\Users\Administrator\Documents\Codex\2026-09-12\compact\outputs"
    r"\NEC-timesheets-2026-08-13-to-2026-09-12-review.json"
)
SNAPSHOT_DIR = ROOT / "data" / "nec_import_snapshots"

LEAVE_STATUSES = {"leave", "sick"}


def normalize_code(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    c = str(raw).strip().upper().replace(" ", "")
    if not c:
        return None
    if c.startswith("C") and c[1:].isdigit():
        return "C" + str(int(c[1:])).zfill(4)
    return c


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def sheet_human_code(sheet: dict) -> Optional[str]:
    emp = sheet.get("employee") or {}
    name = emp.get("name_raw") or emp.get("candidate_name") or ""
    low = name.lower()
    if "chidakwa" in low:
        return "PP058"
    raw = emp.get("mine_no_raw") or emp.get("candidate_employee_code")
    code = normalize_code(raw)
    if code:
        return code
    return None


def load_employees() -> Tuple[List[dict], Dict[str, dict], Dict[str, dict]]:
    rows = supabase.table("employees").select("id,employee_id,first_name,last_name,employment_type").execute().data or []
    by_code: Dict[str, dict] = {}
    by_name: Dict[str, dict] = {}
    for e in rows:
        code = normalize_code(e.get("employee_id"))
        if code:
            by_code[code] = e
        by_name[normalize_name(f"{e.get('first_name', '')} {e.get('last_name', '')}")] = e
    return rows, by_code, by_name


def resolve_employee(sheet: dict, by_code: Dict[str, dict], by_name: Dict[str, dict]) -> Tuple[Optional[dict], str]:
    emp = sheet.get("employee") or {}
    code = sheet_human_code(sheet)
    if code and code in by_code:
        return by_code[code], f"code:{code}"
    name_key = normalize_name(emp.get("name_raw") or emp.get("candidate_name") or "")
    if name_key in by_name:
        return by_name[name_key], f"name:{name_key}"
    return None, f"unresolved code={code} name={name_key}"


def fetch_timesheets(start: str, end: str) -> Dict[str, dict]:
    resp = (
        supabase.table("timesheets")
        .select("*")
        .gte("date", start)
        .lte("date", end)
        .execute()
    )
    out: Dict[str, dict] = {}
    for row in resp.data or []:
        out[f"{row['employee_id']}:{row['date']}"] = row
    return out


def fetch_leaves() -> List[dict]:
    return supabase.table("leaves").select("*").execute().data or []


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
        # Module owns the day — only night / standby from scan
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
    patch = {
        "status": status,
        "regular_hours": reg,
        "overtime_hours": 0,
        "holiday_overtime_hours": 0,
    }
    if night_h is not None and night_h > 0:
        patch["nightshift_hours"] = float(night_h)
        patch["nightshift_allowance"] = True
    elif existing and existing.get("nightshift_allowance"):
        pass  # do not clear allowance on null
    if interp.get("standby_marked") is True:
        patch["standby_allowance"] = True

    nh = float(patch.get("nightshift_hours") or (existing or {}).get("nightshift_hours") or 0)
    patch["total_hours"] = reg + nh

    if existing:
        unchanged = all(existing.get(k) == v for k, v in patch.items())
        if unchanged:
            return None, "skip_unchanged"
    return patch, "upsert"


def apply_patch(db_emp_id: int, ds: str, patch: dict, existing: Optional[dict], apply: bool) -> str:
    if not apply:
        return "dry_run"
    now = datetime.now(timezone.utc).isoformat()
    if existing and existing.get("id"):
        data = {**patch, "updated_at": now}
        supabase.table("timesheets").update(data).eq("id", existing["id"]).execute()
        return "updated"
    insert = {
        "employee_id": db_emp_id,
        "date": ds,
        "start_time": None,
        "end_time": None,
        "callout_overtime_hours": 0,
        "callout_count": 0,
        "standby_allowance": patch.get("standby_allowance", False),
        "nightshift_allowance": patch.get("nightshift_allowance", False),
        "nightshift_hours": patch.get("nightshift_hours", 0),
        "overtime_hours": 0,
        "holiday_overtime_hours": 0,
        "overtime_periods": [],
        "created_at": now,
        "updated_at": now,
        **patch,
    }
    supabase.table("timesheets").insert(insert).execute()
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-json", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    review = json.loads(args.review_json.read_text(encoding="utf-8"))
    sheets = [s for s in review.get("sheets", []) if s.get("disposition") != "superseded"]

    _, by_code, by_name = load_employees()
    existing_ts = fetch_timesheets(PERIOD_START, PERIOD_END)
    leaves = fetch_leaves()

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snap_path = SNAPSHOT_DIR / f"snapshot_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    snap_path.write_text(json.dumps(existing_ts, indent=2), encoding="utf-8")

    report: Dict[str, Any] = {
        "period": {"start": PERIOD_START, "end": PERIOD_END},
        "apply": args.apply,
        "snapshot": str(snap_path),
        "employees": [],
        "stats": {"created": 0, "updated": 0, "skipped": 0, "planned_create": 0, "planned_update": 0, "exceptions": []},
    }

    for sheet in sheets:
        emp_row, match_evidence = resolve_employee(sheet, by_code, by_name)
        emp_report: Dict[str, Any] = {
            "sheet_id": sheet.get("sheet_id"),
            "name": (sheet.get("employee") or {}).get("name_raw"),
            "human_code": sheet_human_code(sheet),
            "match_evidence": match_evidence,
            "database_id": emp_row["id"] if emp_row else None,
            "actions": [],
        }
        if not emp_row:
            report["stats"]["exceptions"].append(emp_report)
            report["employees"].append(emp_report)
            continue

        db_id = int(emp_row["id"])
        human = normalize_code(emp_row.get("employee_id")) or emp_row.get("employee_id")

        for row in sheet.get("rows", []):
            ds = row["date"]
            if ds < PERIOD_START or ds > PERIOD_END:
                continue
            key = f"{db_id}:{ds}"
            existing = existing_ts.get(key)
            patch, reason = build_patch(row, existing, human, leaves)
            if patch is None:
                report["stats"]["skipped"] += 1
                if reason not in ("skip_unchanged", "skip_module_leave_day"):
                    emp_report["actions"].append({"date": ds, "result": reason})
                continue
            action = apply_patch(db_id, ds, patch, existing, args.apply)
            if action == "created":
                report["stats"]["created"] += 1
            elif action == "updated":
                report["stats"]["updated"] += 1
            elif action == "dry_run":
                if existing and existing.get("id"):
                    report["stats"]["planned_update"] += 1
                else:
                    report["stats"]["planned_create"] += 1
            else:
                report["stats"]["skipped"] += 1
            emp_report["actions"].append({"date": ds, "result": action, "reason": reason, "patch": patch})

        report["employees"].append(emp_report)

    out_report = SNAPSHOT_DIR / f"report_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(out_report), "stats": report["stats"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
