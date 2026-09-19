"""Apply validated patches to timesheets with snapshot provenance."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db_helpers import fetch_all_pages
from app.nec_import.employee_match import index_employees, normalize_code, resolve_employee, sheet_human_code
from app.nec_import.patch_builder import build_patch
from app.nec_import.sheet_selection import duplicate_sheet_groups, eligible_review_sheets
from app.supabase_client import supabase


def fetch_timesheets_map(start: str, end: str) -> Dict[str, dict]:
    def _base():
        return (
            supabase.table("timesheets")
            .select("*")
            .gte("date", start)
            .lte("date", end)
            .order("date", desc=False)
            .order("id", desc=False)
        )

    rows = fetch_all_pages(lambda s, e: _base().range(s, e).execute())
    out: Dict[str, dict] = {}
    for row in rows:
        key = f"{row['employee_id']}:{row['date']}"
        if key in out:
            raise RuntimeError(f"Duplicate timesheet key {key} — resolve before import")
        out[key] = row
    return out


def load_employees() -> List[dict]:
    return supabase.table("employees").select(
        "id,employee_id,first_name,last_name,employment_type,is_active"
    ).execute().data or []


def fetch_leaves() -> List[dict]:
    return supabase.table("leaves").select("*").execute().data or []


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


def run_import_from_review(
    review: dict,
    *,
    period_start: str,
    period_end: str,
    apply: bool,
    snapshot_dir,
) -> Dict[str, Any]:
    sheets = eligible_review_sheets(review.get("sheets", []))
    conflicts = duplicate_sheet_groups(sheets)
    by_code, by_name = index_employees(load_employees())
    existing_ts = fetch_timesheets_map(period_start, period_end)
    leaves = fetch_leaves()

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snapshot_dir / f"snapshot_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    import json
    snap_path.write_text(json.dumps(existing_ts, indent=2), encoding="utf-8")

    report: Dict[str, Any] = {
        "period": {"start": period_start, "end": period_end},
        "apply": apply,
        "snapshot": str(snap_path),
        "duplicate_sheet_groups": conflicts,
        "employees": [],
        "stats": {
            "created": 0, "updated": 0, "skipped": 0,
            "planned_create": 0, "planned_update": 0, "exceptions": [],
        },
    }

    if conflicts and apply:
        report["aborted"] = True
        report["abort_reason"] = "duplicate_sheet_groups"
        return report

    for sheet in sheets:
        emp_row, match_evidence, amb = resolve_employee(sheet, by_code, by_name)
        emp_report: Dict[str, Any] = {
            "sheet_id": sheet.get("sheet_id"),
            "name": (sheet.get("employee") or {}).get("name_raw"),
            "human_code": sheet_human_code(sheet),
            "match_evidence": match_evidence,
            "database_id": emp_row["id"] if emp_row else None,
            "ambiguous_candidates": [
                {"id": c.get("id"), "employee_id": c.get("employee_id"), "name": f"{c.get('first_name')} {c.get('last_name')}"}
                for c in amb
            ],
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
            if ds < period_start or ds > period_end:
                continue
            key = f"{db_id}:{ds}"
            existing = existing_ts.get(key)
            patch, reason = build_patch(row, existing, human, leaves)
            if patch is None:
                report["stats"]["skipped"] += 1
                if reason not in ("skip_unchanged", "skip_module_leave_day"):
                    emp_report["actions"].append({
                        "date": ds, "result": reason,
                        "source": row.get("source"),
                        "interpreted": row.get("interpreted"),
                    })
                continue
            action = apply_patch(db_id, ds, patch, existing, apply)
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
            emp_report["actions"].append({
                "date": ds, "result": action, "reason": reason, "patch": patch,
                "existing_id": existing.get("id") if existing else None,
            })

        report["employees"].append(emp_report)

    return report
