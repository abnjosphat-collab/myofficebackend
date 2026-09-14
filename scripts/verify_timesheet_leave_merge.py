"""Audit leave → NEC timesheet grid projection for one payroll period."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from app.nec_import.employee_match import normalize_code
from app.supabase_client import supabase

NEC_PERIOD = (date(2026, 8, 13), date(2026, 9, 12))
SALARIED_SEP_2026 = (date(2026, 9, 1), date(2026, 9, 30))

LEAVE_TYPE_TO_STATUS = {
    "annual": "leave",
    "sick": "sick",
    "compassionate": "special_leave",
    "emergency": "special_leave",
    "maternity": "maternity",
    "study": "study",
    "lieu": "lieu",
}

INCLUDE_UNSIGNED = True  # necModuleBatch Aug–Sep 2026


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


PERIOD_START = NEC_PERIOD[0]
PERIOD_END = NEC_PERIOD[1]


def period_days() -> list[date]:
    return list(daterange(PERIOD_START, PERIOD_END))


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--salaried", action="store_true", help="Audit calendar Sep 2026 salaried roster")
    args = p.parse_args()

    global PERIOD_START, PERIOD_END
    if args.salaried:
        PERIOD_START, PERIOD_END = SALARIED_SEP_2026
        roster_label = "SALARIED"
    else:
        PERIOD_START, PERIOD_END = NEC_PERIOD
        roster_label = "NEC"

    day_strs = [d.isoformat() for d in period_days()]
    day_set = set(day_strs)

    emps = supabase.table("employees").select("id,employee_id,first_name,last_name,employment_type").execute().data or []
    by_code: dict[str, dict] = {}
    roster_db_ids: set[str] = set()
    for e in emps:
        code = normalize_code(e.get("employee_id")) or (e.get("employee_id") or "").strip().upper()
        if code:
            by_code[code] = e
            raw = (e.get("employee_id") or "").strip().upper()
            if raw and raw not in by_code:
                by_code[raw] = e
        if (e.get("employment_type") or "").upper() == roster_label:
            roster_db_ids.add(str(e["id"]))

    leaves = supabase.table("leaves").select("*").execute().data or []
    if INCLUDE_UNSIGNED:
        leaves = [l for l in leaves if l.get("status") != "rejected"]
    else:
        leaves = [l for l in leaves if l.get("status") == "approved"]

    overlapping = [
        l
        for l in leaves
        if l.get("start_date", "") <= PERIOD_END.isoformat()
        and l.get("end_date", "") >= PERIOD_START.isoformat()
    ]

    projected_days = 0
    skipped: list[tuple[str, dict]] = []
    applied: list[tuple[str, dict, int]] = []

    for lv in overlapping:
        code_raw = lv.get("employee_id") or ""
        code = normalize_code(code_raw) or code_raw.strip().upper()
        emp = by_code.get(code) or by_code.get(code_raw.strip().upper())
        reasons: list[str] = []
        if not emp:
            reasons.append("no_employee_match")
        else:
            db_id = str(emp["id"])
            if db_id not in roster_db_ids:
                reasons.append(f"not_{roster_label}_roster({emp.get('employment_type')})")
        lt = lv.get("leave_type") or ""
        if lt not in LEAVE_TYPE_TO_STATUS:
            reasons.append(f"unmapped_leave_type({lt})")
        days_in_period = [
            ds
            for ds in day_strs
            if lv.get("start_date", "") <= ds <= lv.get("end_date", "")
        ]
        if not days_in_period:
            reasons.append("no_days_in_period")

        if reasons:
            skipped.append((", ".join(reasons), lv))
            continue

        name = f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip()
        applied.append((name, lv, len(days_in_period)))
        projected_days += len(days_in_period)

    print(f"{roster_label} period {PERIOD_START} .. {PERIOD_END} ({len(day_strs)} days)")
    print(f"{roster_label} employees on roster: {len(roster_db_ids)}")
    print(f"Leave records overlapping period: {len(overlapping)}")
    print(f"Projected leave cells (person-days): {projected_days}")
    print(f"Skipped leave records: {len(skipped)}")
    print()

    if applied:
        print(f"=== APPLIED (would show on {roster_label} grid) ===")
        for name, lv, n in sorted(applied, key=lambda x: (x[0], x[1]["start_date"])):
            print(
                f"  {name} ({lv.get('employee_id')}) {lv['start_date']}..{lv['end_date']} "
                f"{lv.get('leave_type')} [{lv.get('status')}] -> {n} day(s)"
            )
    print()

    if skipped:
        print(f"=== SKIPPED (will NOT show on {roster_label} grid) ===")
        for reason, lv in sorted(skipped, key=lambda x: x[1].get("start_date", "")):
            em = supabase.table("employees").select("first_name,last_name,employment_type").eq(
                "employee_id", lv.get("employee_id")
            ).limit(1).execute()
            name = ""
            if em.data:
                name = f"{em.data[0].get('first_name','')} {em.data[0].get('last_name','')}".strip()
            print(
                f"  [{reason}] {name or '?'} ({lv.get('employee_id')}) "
                f"{lv.get('start_date')}..{lv.get('end_date')} {lv.get('leave_type')} [{lv.get('status')}]"
            )

    # NEC employees with leave in period but zero projected (shouldn't happen if logic right)
    print()
    print(f"=== Sample: {roster_label} leave days vs saved timesheet status (first 15 applied) ===")
    for name, lv, _ in applied[:15]:
        emp = by_code.get(normalize_code(lv.get("employee_id")) or "")
        if not emp:
            continue
        db_id = emp["id"]
        days_in_period = [
            ds
            for ds in day_strs
            if lv.get("start_date", "") <= ds <= lv.get("end_date", "")
        ]
        ts = (
            supabase.table("timesheets")
            .select("date,status,regular_hours")
            .eq("employee_id", db_id)
            .in_("date", days_in_period[:5])
            .execute()
            .data
            or []
        )
        ts_by = {r["date"]: r for r in ts}
        expect = LEAVE_TYPE_TO_STATUS[lv["leave_type"]]
        mism = []
        for ds in days_in_period[:5]:
            row = ts_by.get(ds)
            if row and row.get("status") not in (expect, "leave", "sick", "special_leave", "maternity", "study", "lieu"):
                mism.append(f"{ds}:saved={row.get('status')}")
            elif not row:
                mism.append(f"{ds}:no_saved_row(auto_leave_ok)")
        print(f"  {name} {lv['start_date']}..{lv['end_date']}: {mism or ['merge_only_ok']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
