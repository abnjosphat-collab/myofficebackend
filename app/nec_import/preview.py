"""Build import preview: proposed changes, conflicts, missing sheets."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Set

from app.nec_import.apply_runner import fetch_leaves, fetch_timesheets_map, load_employees
from app.nec_import.employee_match import index_employees, normalize_code, resolve_employee, sheet_human_code
from app.nec_import.patch_builder import build_patch
from app.nec_import.period import iter_period_dates


def build_preview(review: dict, period_start: str, period_end: str) -> Dict[str, Any]:
    sheets = [s for s in review.get("sheets", []) if s.get("disposition") != "superseded"]
    employees = load_employees()
    _, by_code, by_name = index_employees(employees)
    existing_ts = fetch_timesheets_map(period_start, period_end)
    leaves = fetch_leaves()
    period_dates = iter_period_dates(date.fromisoformat(period_start), date.fromisoformat(period_end))

    nec_active = [
        e for e in employees
        if (e.get("employment_type") or "").upper() == "NEC" and e.get("is_active") is not False
    ]

    matched_db_ids: Set[int] = set()
    sheet_summaries: List[dict] = []
    line_items: List[dict] = []

    for sheet in sheets:
        emp_row, match_evidence, amb = resolve_employee(sheet, by_code, by_name)
        sid = sheet.get("sheet_id")
        summary = {
            "sheet_id": sid,
            "human_code": sheet_human_code(sheet),
            "name": (sheet.get("employee") or {}).get("name_raw"),
            "disposition": sheet.get("disposition"),
            "source_pages": sheet.get("source_pages") or sheet.get("pages"),
            "match_evidence": match_evidence,
            "database_id": emp_row["id"] if emp_row else None,
            "ambiguous_candidates": amb,
            "status": "matched" if emp_row else "unresolved_identity",
        }
        sheet_summaries.append(summary)

        if not emp_row:
            continue
        db_id = int(emp_row["id"])
        matched_db_ids.add(db_id)
        human = normalize_code(emp_row.get("employee_id")) or emp_row.get("employee_id")

        for row in sheet.get("rows", []):
            ds = row["date"]
            if ds not in period_dates:
                continue
            key = f"{db_id}:{ds}"
            existing = existing_ts.get(key)
            patch, reason = build_patch(row, existing, human, leaves)
            kind = "skip"
            if patch is not None:
                if existing and existing.get("id"):
                    kind = "update" if reason == "upsert" else "update"
                else:
                    kind = "create"
            elif reason == "skip_unchanged":
                kind = "unchanged"
            elif reason == "skip_unresolved_normal":
                kind = "unresolved"
            elif reason.startswith("skip_leave") or reason == "skip_unmapped_status":
                kind = "exception"

            line_items.append({
                "sheet_id": sid,
                "employee_id": db_id,
                "human_code": human,
                "date": ds,
                "kind": kind,
                "reason": reason,
                "patch": patch,
                "existing": {k: existing.get(k) for k in ("id", "status", "regular_hours", "nightshift_hours", "standby_allowance")} if existing else None,
                "source": row.get("source"),
                "interpreted": row.get("interpreted"),
                "scan_ot_1_5_reference": row.get("scan_ot_1_5_hours_reference_only"),
                "scan_ot_2_0_reference": row.get("scan_ot_2_0_hours_reference_only"),
            })

    missing_sheets = []
    for e in nec_active:
        eid = int(e["id"])
        if eid not in matched_db_ids:
            missing_sheets.append({
                "database_id": eid,
                "human_code": normalize_code(e.get("employee_id")),
                "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
            })

    dup_groups: Dict[str, List[str]] = {}
    for s in sheets:
        code = sheet_human_code(s) or (s.get("employee") or {}).get("name_raw") or "unknown"
        dup_groups.setdefault(code, []).append(s.get("sheet_id") or "?")
    conflicts = [
        {"key": k, "sheet_ids": v}
        for k, v in dup_groups.items()
        if len(v) > 1
    ]

    stats = {
        "create": sum(1 for x in line_items if x["kind"] == "create"),
        "update": sum(1 for x in line_items if x["kind"] == "update"),
        "unchanged": sum(1 for x in line_items if x["kind"] == "unchanged"),
        "unresolved": sum(1 for x in line_items if x["kind"] == "unresolved"),
        "exception": sum(1 for x in line_items if x["kind"] == "exception"),
    }

    return {
        "period": {"start": period_start, "end": period_end},
        "schema_version": review.get("schema_version"),
        "extraction_version": review.get("extraction_version") or review.get("source", {}).get("method"),
        "sheet_summaries": sheet_summaries,
        "line_items": line_items,
        "missing_sheets": missing_sheets,
        "duplicate_sheet_groups": conflicts,
        "stats": stats,
    }
