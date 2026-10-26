# app/machine_history.py — one machine's history (M6): its jobs, breakdowns,
# planned work, spares, hours and its measures. The matching is pure (rows in,
# history out) so it can be tested with a hand-checked answer; the endpoint in
# routers/equipment.py only reads the rows (with scope) and calls
# compute_machine_history. The written definitions live in
# frontend/docs/WORK_ORDERS.md ("Machine history (M6)").
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from app.maintenance_links import parse_routine_marker
from app.maintenance_measures import (
    TERMINAL_STATUSES,
    machine_downtime,
    parse_date,
    parse_hours,
    pm_on_time,
)


def _norm(text: Any) -> str:
    """A machine name as the history matches it: trimmed, case-insensitive."""
    if not isinstance(text, (str, int, float)):
        return ""
    return str(text or "").strip().casefold()


def _same_id(value: Any, equipment_id: int) -> bool:
    if value is None:
        return False
    return str(value).strip() == str(equipment_id)


def job_matches(order: dict, equipment_id: int, name_fold: str, links_active: bool) -> bool:
    """Whether a work order is this machine's: the register link when the M1
    columns exist, else its routine marker, else its normalized machine name."""
    if links_active and _same_id(order.get("equipment_id"), equipment_id):
        return True
    if parse_routine_marker(order.get("equipment_info")) == equipment_id:
        return True
    return name_fold != "" and _norm(order.get("equipment_info")) == name_fold


def schedule_matches(schedule: dict, equipment_id: int, name_fold: str, links_active: bool) -> bool:
    """Whether a schedule is this machine's: same three ways as a job."""
    if links_active and _same_id(schedule.get("equipment_id"), equipment_id):
        return True
    if parse_routine_marker(schedule.get("equipment_info")) == equipment_id:
        return True
    return name_fold != "" and _norm(schedule.get("equipment_info")) == name_fold


def breakdown_matches(row: dict, name_fold: str) -> bool:
    """Whether a breakdown is this machine's: its normalized machine name.

    Breakdowns carry no equipment_id (M6 does not add one), so this is the
    same name match the M5 measures use — deliberate, not a guess: matching is
    documented in WORK_ORDERS.md.
    """
    return name_fold != "" and _norm(row.get("machine_name")) == name_fold


def _as_list(value: Any) -> list:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
    return value if isinstance(value, list) else []


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def aggregate_spares(orders: list, breakdowns: list) -> dict:
    """Spare parts over the machine's jobs and breakdowns, by normalized name.

    Work-order lines carry {name, quantity, unit_cost}; breakdown lines carry
    {name, quantity, unit_price, total_cost} (or a JSON string of those). A
    line with no name is skipped: it cannot be listed, so it cannot be
    counted either. Sorted by cost, highest first.
    """
    acc: dict = {}

    def add(key: str, name: str, quantity: float, cost: float, order_id: Any, breakdown_id: Any) -> None:
        entry = acc.setdefault(key, {"name": name, "quantity": 0.0, "cost": 0.0, "orders": set(), "breakdowns": set()})
        entry["quantity"] += quantity
        entry["cost"] += cost
        if order_id is not None:
            entry["orders"].add(order_id)
        if breakdown_id is not None:
            entry["breakdowns"].add(breakdown_id)

    for order in orders:
        for spare in _as_list(order.get("spares_used")):
            if not isinstance(spare, dict):
                continue
            name = str(spare.get("name") or "").strip()
            if not name:
                continue
            quantity = _num(spare.get("quantity"))
            add(name.casefold(), name, quantity, quantity * _num(spare.get("unit_cost")), order.get("id"), None)
    for row in breakdowns:
        for spare in _as_list(row.get("spares_used")):
            if not isinstance(spare, dict):
                continue
            name = str(spare.get("name") or "").strip()
            if not name:
                continue
            quantity = _num(spare.get("quantity"))
            total = spare.get("total_cost")
            cost = _num(total) if total not in (None, "") else quantity * _num(spare.get("unit_price"))
            add(name.casefold(), name, quantity, cost, None, row.get("id"))
    rows = [
        {
            "name": entry["name"],
            "quantity": round(entry["quantity"], 2),
            "cost": round(entry["cost"], 2),
            "jobs": len(entry["orders"]),
            "breakdowns": len(entry["breakdowns"]),
        }
        for entry in acc.values()
    ]
    rows.sort(key=lambda r: (-r["cost"], r["name"]))
    return {"rows": rows, "total_cost": round(sum(r["cost"] for r in rows), 2)}


def repeat_failures_for_machine(jobs: list, today: date, machine: str) -> dict:
    """This machine's repeat failures: breakdown jobs sharing a Problem code.

    The same rule as maintenance_measures.repeat_failures (two or more coded
    breakdown jobs in the trailing 90 days; "30-day" when the whole span fits
    in 30 days), but every job here is this machine's already, so groups form
    by code alone however each job matched — an id-linked job and a
    name-matched job with the same code still meet.
    """
    start = today - timedelta(days=90)
    grouped: dict = {}
    # Oldest first (the list arrives newest first): the group keeps the
    # original report's spelling no matter what arrives later.
    for job in reversed(jobs):
        if (job.get("classification") or "") != "breakdown":
            continue
        code = (job.get("failure_mode") or "").strip()
        if not code:
            continue
        raised = parse_date(job.get("date_raised"))
        if raised is None or raised < start or raised > today:
            continue
        entry = grouped.setdefault(code.casefold(), {"code": code, "events": []})
        entry["events"].append((raised, job.get("id")))
    out = []
    for entry in grouped.values():
        events = sorted(entry["events"], key=lambda e: (e[0], str(e[1])))
        if len(events) < 2:
            continue
        span = (events[-1][0] - events[0][0]).days
        out.append({
            "machine": machine,
            "code": entry["code"],
            "count": len(events),
            "window_days": 30 if span <= 30 else 90,
            "first": events[0][0].isoformat(),
            "last": events[-1][0].isoformat(),
            "ids": [i for _, i in events],
        })
    out.sort(key=lambda g: (-g["count"], g["code"] or ""))
    return {
        "groups_30d": sum(1 for g in out if g["window_days"] == 30),
        "groups_90d": sum(1 for g in out if g["window_days"] == 90),
        "groups": out,
    }


def compute_machine_history(
    equipment: dict,
    orders: list,
    breakdowns: list,
    schedules: list,
    today: date,
    days: int = 90,
    links_active: bool = True,
) -> dict:
    """The machine's history: all-time lists and totals, trailing-window measures.

    Lists (jobs, breakdowns, schedules, spares) and totals cover everything
    ever recorded, so each total reconciles with the list beside it. The
    measures reuse the M5 definitions over the trailing `days` window.
    """
    equipment_id = equipment.get("id")
    try:
        equipment_id = int(equipment_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        equipment_id = -1
    name = (equipment.get("name") or "").strip()
    name_fold = name.casefold()

    jobs = [o for o in orders if job_matches(o, equipment_id, name_fold, links_active)]
    jobs.sort(key=lambda o: parse_date(o.get("date_raised")) or date.min, reverse=True)
    bds = [b for b in breakdowns if breakdown_matches(b, name_fold)]
    bds.sort(key=lambda b: parse_date(b.get("breakdown_date")) or date.min, reverse=True)
    scheds = [s for s in schedules if schedule_matches(s, equipment_id, name_fold, links_active)]
    scheds.sort(key=lambda s: (not s.get("active"), parse_date(s.get("next_due_date")) or date.max, (s.get("name") or "")))

    spares = aggregate_spares(jobs, bds)
    labour_hours = round(sum(parse_hours(o.get("total_time_worked")) for o in jobs), 1)
    downtime_hours = round(sum(_num(b.get("downtime_minutes")) for b in bds) / 60, 1)
    counts_by_status: dict = {}
    for order in jobs:
        status = (order.get("status") or "").strip() or "unknown"
        counts_by_status[status] = counts_by_status.get(status, 0) + 1

    start = today - timedelta(days=days)
    downtime_rows = machine_downtime(bds, start, today)["rows"]
    repeats = repeat_failures_for_machine(jobs, today, name or "Unknown machine")

    return {
        "equipment": equipment,
        "period": {"days": days, "start": start.isoformat(), "end": today.isoformat()},
        "links_active": links_active,
        "totals": {
            "jobs": len(jobs),
            "open_jobs": sum(1 for o in jobs if (o.get("status") or "") not in TERMINAL_STATUSES),
            "breakdowns": len(bds),
            "schedules": len(scheds),
            "labour_hours": labour_hours,
            "spares_cost": spares["total_cost"],
            "downtime_hours": downtime_hours,
            "counts_by_status": counts_by_status,
        },
        "jobs": jobs,
        "breakdowns": bds,
        "schedules": scheds,
        "spares": spares["rows"],
        "measures": {
            "pm_on_time": pm_on_time(jobs, start, today),
            "downtime": downtime_rows[0] if downtime_rows else None,
            "repeat_failures": repeats,
        },
    }
