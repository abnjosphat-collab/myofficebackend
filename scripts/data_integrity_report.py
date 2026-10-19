"""Read-only report of what is already wrong in the data: duplicates, overlaps, orphans, missing approvers, stray spaces.

Usage (from backend/):
    PYTHONPATH=. .venv/Scripts/python.exe scripts/data_integrity_report.py                 # summary on screen
    PYTHONPATH=. .venv/Scripts/python.exe scripts/data_integrity_report.py --out report.md # full list with names, to a file

It only READS (the service-role key from backend/.env). It changes nothing and deletes nothing: the people who own the payroll
and leave records decide what to keep. It uses the same rules the server now enforces for new records (app/overtime_conflicts.py,
app/leave_conflicts.py, app/ppe_conflicts.py), so "what the report finds" and "what the server would refuse" cannot disagree.
Write the --out file OUTSIDE the repository: it contains employee names.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from typing import Any, Dict, List

from app.db_helpers import fetch_all_pages
from app.leave_conflicts import find_conflict as leave_conflict
from app.overtime_conflicts import find_conflict as overtime_conflict
from app.ppe_conflicts import find_duplicate as ppe_duplicate
from app.supabase_client import supabase, rows


def _all(table: str, columns: str) -> List[Dict[str, Any]]:
    return fetch_all_pages(lambda s, e: supabase.table(table).select(columns).order("id").range(s, e).execute(), extract_rows=rows)


def _by_person(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        grouped[str(r.get("employee_id"))].append(r)
    return grouped


def overtime_findings(records):
    exact, overlaps = [], []
    for emp, items in _by_person(records).items():
        live = [r for r in items if str(r.get("status") or "").lower() not in ("rejected", "cancelled") and r.get("start_time") and r.get("end_time")]
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if overtime_conflict([b], a["date"], a["start_time"], a["end_time"]):
                    same = (a["date"], a["start_time"], a["end_time"]) == (b["date"], b["start_time"], b["end_time"])
                    (exact if same else overlaps).append((a, b))
    return exact, overlaps


def leave_findings(records):
    exact, overlaps = [], []
    for emp, items in _by_person(records).items():
        live = [r for r in items if str(r.get("status") or "").lower() not in ("rejected", "cancelled")]
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if leave_conflict([b], a["start_date"], a["end_date"]):
                    same = (a["start_date"], a["end_date"]) == (b["start_date"], b["end_date"])
                    (exact if same else overlaps).append((a, b))
    return exact, overlaps


def ppe_findings(records):
    pairs = []
    for emp, items in _by_person(records).items():
        for i, a in enumerate(items):
            if str(a.get("status") or "active").lower() in ("returned", "replaced", "lost", "disposed", "cancelled"):
                continue
            if ppe_duplicate(items[i + 1:], a.get("ppe_type"), a.get("issue_date")):
                pairs.append(a)
    return pairs


def main(out_path: str | None) -> int:
    employees = _all("employees", "id,employee_id,first_name,last_name")
    known = {str(e.get("employee_id")) for e in employees}
    overtime = _all("overtime", "id,employee_id,employee_name,date,start_time,end_time,status,approved_by")
    leaves = _all("leaves", "id,employee_id,employee_name,start_date,end_date,status,leave_type")
    ppe = _all("ppe_records", "id,employee_id,employee_name,ppe_type,issue_date,status")

    ot_exact, ot_overlap = overtime_findings(overtime)
    lv_exact, lv_overlap = leave_findings(leaves)
    ppe_dupes = ppe_findings(ppe)
    orphans = {
        "overtime": [r for r in overtime if str(r.get("employee_id")) not in known],
        "leaves": [r for r in leaves if str(r.get("employee_id")) not in known],
        "ppe_records": [r for r in ppe if str(r.get("employee_id")) not in known],
    }
    no_approver = [r for r in overtime if str(r.get("status")) in ("approved", "paid") and not (r.get("approved_by") or "").strip()]
    spaced = [e for e in employees if any(" ".join(str(e.get(k) or "").split()) != str(e.get(k) or "") for k in ("first_name", "last_name"))]

    summary = [
        ("Overtime: identical live requests (pairs)", len(ot_exact)),
        ("Overtime: partly overlapping live requests (pairs)", len(ot_overlap)),
        ("Overtime: approved or paid with no approver recorded", len(no_approver)),
        ("Overtime: employee id matches nobody", len(orphans["overtime"])),
        ("Leaves: identical live requests (pairs)", len(lv_exact)),
        ("Leaves: partly overlapping live requests (pairs)", len(lv_overlap)),
        ("Leaves: employee id matches nobody", len(orphans["leaves"])),
        ("PPE: the same item issued twice on one day", len(ppe_dupes)),
        ("PPE: employee id matches nobody", len(orphans["ppe_records"])),
        ("Employees: stray spaces in the name", len(spaced)),
    ]
    for label, n in summary:
        print(f"{n:5d}  {label}")

    if out_path:
        lines = ["# Data integrity report", "", "Read-only. Nothing has been changed or deleted. Decide what to keep; an approved duplicate may already have been counted.", ""]
        lines += ["| Finding | Count |", "|---|---:|"] + [f"| {label} | {n} |" for label, n in summary] + [""]

        def pair_section(title, pairs, fmt):
            lines.append(f"## {title}")
            lines.append("")
            lines.extend(f"- {fmt(a)}  <->  {fmt(b)}" for a, b in pairs) if pairs else lines.append("None.")
            lines.append("")

        ot = lambda r: f"#{r['id']} {r.get('employee_name')} ({r.get('employee_id')}) {r['date']} {r.get('start_time')}-{r.get('end_time')} [{r.get('status')}]"
        lv = lambda r: f"#{r['id']} {r.get('employee_name')} ({r.get('employee_id')}) {r['start_date']} to {r['end_date']} {r.get('leave_type')} [{r.get('status')}]"
        pair_section("Overtime: identical", ot_exact, ot)
        pair_section("Overtime: partly overlapping", ot_overlap, ot)
        pair_section("Leaves: identical", lv_exact, lv)
        pair_section("Leaves: partly overlapping", lv_overlap, lv)
        lines += ["## PPE: the same item issued twice on one day", ""]
        lines += [f"- #{r['id']} {r.get('employee_name')} ({r.get('employee_id')}) {r.get('ppe_type')} {r.get('issue_date')}" for r in ppe_dupes] or ["None."]
        lines += ["", "## Records whose employee id matches nobody", ""]
        for table, items in orphans.items():
            lines += [f"- {table} #{r['id']}: {r.get('employee_name')} ({r.get('employee_id')})" for r in items]
        lines += ["", "## Overtime approved or paid with no approver recorded (count by month)", ""]
        months: Dict[str, int] = defaultdict(int)
        for r in no_approver:
            months[str(r.get("date"))[:7]] += 1
        lines += [f"- {m}: {n}" for m, n in sorted(months.items())] or ["None."]
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        print(f"\nFull list written to {out_path} (contains names: keep it outside the repository).")
    return 0


if __name__ == "__main__":
    target = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    sys.exit(main(target))
