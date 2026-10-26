# app/maintenance_measures.py — the maintenance measures, computed on the server
# (M5). Each function is pure (rows in, figures out) so its definition can be
# tested with a hand-checked answer; the endpoint in routers/maintenance.py
# only reads the rows (with scope) and calls compute_measures. The written
# definitions live in frontend/docs/WORK_ORDERS.md ("Measures (M5)").
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

TERMINAL_STATUSES = ("completed", "cancelled", "not-done")

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*h(?:\s*(\d+(?:\.\d+)?)\s*m)?", re.IGNORECASE)


def parse_date(value: Any) -> Optional[date]:
    """A YYYY-MM-DD (or ISO datetime) value as a date; None when unknowable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_hours(value: Any) -> float:
    """A worked duration as hours: "7h 30m", "7h", or a bare number."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    match = _DURATION_RE.search(text)
    if match:
        hours = float(match.group(1))
        if match.group(2):
            hours += float(match.group(2)) / 60
        return hours
    try:
        return float(text)
    except ValueError:
        return 0.0


def completion_date(order: dict) -> Optional[date]:
    """When the artisan closed the job: the M1 stamp, else the artisan's date."""
    return parse_date(order.get("completed_at")) or parse_date(order.get("artisan_date"))


def machine_key(order: dict) -> tuple:
    """(kind, key, label): the register id when linked, else the typed name."""
    equipment_id = order.get("equipment_id")
    if equipment_id is not None and str(equipment_id).strip() != "":
        return ("id", equipment_id, None)
    name = (order.get("equipment_info") or "").strip()
    return ("name", name.casefold(), name or "Unknown machine")


def machine_label(key: tuple, equipment_names: dict) -> str:
    kind, ref, raw = key
    if kind == "id":
        try:
            return equipment_names.get(int(ref)) or equipment_names.get(str(ref)) or f"Equipment #{ref}"
        except (TypeError, ValueError):
            return f"Equipment #{ref}"
    return raw or "Unknown machine"


def _spares_list(value: Any) -> list:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
    return value if isinstance(value, list) else []


def spares_cost(value: Any) -> float:
    """Quantity x unit cost over a spares list (work-order unit_cost shape)."""
    total = 0.0
    for spare in _spares_list(value):
        if not isinstance(spare, dict):
            continue
        try:
            quantity = float(spare.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        try:
            unit_cost = float(spare.get("unit_cost") or 0)
        except (TypeError, ValueError):
            unit_cost = 0.0
        total += quantity * unit_cost
    return total


def pm_on_time(orders: list, start: date, end: date) -> dict:
    """Planned jobs due in [start, end] (cancelled excluded): share closed on time."""
    due = [
        o for o in orders
        if (o.get("classification") or "") == "planned_maintenance"
        and (o.get("status") or "") != "cancelled"
        and (parse_date(o.get("due_date")) or date.min) >= start
        and (parse_date(o.get("due_date")) or date.max) <= end
        and parse_date(o.get("due_date")) is not None
    ]
    on_time_ids: list = []
    for order in due:
        done = completion_date(order)
        if (order.get("status") or "") == "completed" and done is not None and done <= (parse_date(order.get("due_date")) or date.min):
            on_time_ids.append(order.get("id"))
    total = len(due)
    return {
        "percent": round(len(on_time_ids) / total * 100) if total else None,
        "on_time": len(on_time_ids),
        "total": total,
        "ids": [o.get("id") for o in due],
        "on_time_ids": on_time_ids,
    }


BACKLOG_BUCKETS = ("0-7 days", "8-30 days", "31-90 days", "Over 90 days", "Date unknown")


def backlog_by_age(orders: list, today: date) -> dict:
    """Open jobs (not completed/cancelled/not-done) by age of date_raised."""
    buckets: dict = {label: [] for label in BACKLOG_BUCKETS}
    for order in orders:
        if (order.get("status") or "") in TERMINAL_STATUSES:
            continue
        raised = parse_date(order.get("date_raised"))
        if raised is None:
            buckets["Date unknown"].append(order.get("id"))
        else:
            age = (today - raised).days
            if age <= 7:
                buckets["0-7 days"].append(order.get("id"))
            elif age <= 30:
                buckets["8-30 days"].append(order.get("id"))
            elif age <= 90:
                buckets["31-90 days"].append(order.get("id"))
            else:
                buckets["Over 90 days"].append(order.get("id"))
    total = sum(len(ids) for ids in buckets.values())
    return {
        "total": total,
        "buckets": [{"label": label, "count": len(buckets[label]), "ids": buckets[label]} for label in BACKLOG_BUCKETS],
    }


def opened_vs_completed(orders: list, weeks: int, today: date) -> dict:
    """Opened (by date_raised) vs completed (by completion date) per Harare week.

    Weeks start on Monday; the last one is the week containing today. Jobs
    whose date is unknowable are counted once in undated_opened /
    undated_completed instead of a week.
    """
    monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=n) for n in range(weeks - 1, -1, -1)]
    rows = [
        {"week_start": s.isoformat(), "opened": 0, "completed": 0, "opened_ids": [], "completed_ids": []}
        for s in starts
    ]
    undated_opened: list = []
    undated_completed: list = []
    for order in orders:
        raised = parse_date(order.get("date_raised"))
        if raised is None:
            undated_opened.append(order.get("id"))
        else:
            for row in rows:
                week_start = date.fromisoformat(row["week_start"])
                if week_start <= raised < week_start + timedelta(days=7):
                    row["opened"] += 1
                    row["opened_ids"].append(order.get("id"))
                    break
        if (order.get("status") or "") == "completed":
            done = completion_date(order)
            if done is None:
                undated_completed.append(order.get("id"))
            else:
                for row in rows:
                    week_start = date.fromisoformat(row["week_start"])
                    if week_start <= done < week_start + timedelta(days=7):
                        row["completed"] += 1
                        row["completed_ids"].append(order.get("id"))
                        break
    return {
        "weeks": rows,
        "undated_opened": undated_opened,
        "undated_completed": undated_completed,
    }


def _breakdown_start_moment(row: dict) -> Optional[datetime]:
    day = parse_date(row.get("breakdown_date"))
    if day is None:
        return None
    moment = datetime(day.year, day.month, day.day)
    start = (row.get("breakdown_start") or "").strip()
    match = re.match(r"^(\d{1,2}):(\d{2})", start)
    if match:
        try:
            return moment.replace(hour=int(match.group(1)), minute=int(match.group(2)))
        except ValueError:
            return moment
    return moment


def machine_downtime(breakdowns: list, start: date, end: date) -> dict:
    """Per machine (by breakdown machine_name): breakdowns, downtime, MTTR, MTBF."""
    by_machine: dict = {}
    for row in breakdowns:
        day = parse_date(row.get("breakdown_date"))
        if day is None or day < start or day > end:
            continue
        name = (row.get("machine_name") or "").strip() or "Unknown machine"
        key = name.casefold()
        entry = by_machine.setdefault(key, {"machine": name, "starts": [], "repairs": [], "downtime": 0.0, "ids": []})
        entry["machine"] = name  # last spelling wins; the key is the match
        entry["ids"].append(row.get("id"))
        try:
            entry["downtime"] += float(row.get("downtime_minutes") or 0)
        except (TypeError, ValueError):
            pass
        repair = row.get("repair_time_minutes")
        try:
            if repair is not None:
                entry["repairs"].append(float(repair))
        except (TypeError, ValueError):
            pass
        moment = _breakdown_start_moment(row)
        if moment is not None:
            entry["starts"].append(moment)
    rows = []
    for entry in by_machine.values():
        starts = sorted(entry["starts"])
        gaps = [(b - a).total_seconds() / 86400 for a, b in zip(starts, starts[1:])]
        rows.append({
            "machine": entry["machine"],
            "breakdowns": len(entry["ids"]),
            "downtime_hours": round(entry["downtime"] / 60, 1),
            "mttr_hours": round(sum(entry["repairs"]) / len(entry["repairs"]) / 60, 1) if entry["repairs"] else None,
            "mtbf_days": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "breakdown_ids": entry["ids"],
        })
    rows.sort(key=lambda r: (-r["breakdowns"], -r["downtime_hours"], r["machine"]))
    return {"rows": rows}


def repeat_failures(orders: list, today: date) -> dict:
    """Breakdown jobs sharing a machine and Problem code within 90 days.

    The window is 30 when the whole group spans 30 days or less, else 90.
    """
    start = today - timedelta(days=90)
    groups: dict = {}
    for order in orders:
        if (order.get("classification") or "") != "breakdown":
            continue
        code = (order.get("failure_mode") or "").strip()
        if not code:
            continue
        raised = parse_date(order.get("date_raised"))
        if raised is None or raised < start or raised > today:
            continue
        key = (machine_key(order)[:2], code.casefold())
        groups.setdefault(key, {"machine_key": machine_key(order), "code": code, "events": []})
        groups[key]["events"].append((raised, order.get("id")))
    out = []
    for group in groups.values():
        events = sorted(group["events"])
        if len(events) < 2:
            continue
        span = (events[-1][0] - events[0][0]).days
        out.append({
            "machine_key": group["machine_key"],
            "code": group["code"],
            "count": len(events),
            "window_days": 30 if span <= 30 else 90,
            "first": events[0][0].isoformat(),
            "last": events[-1][0].isoformat(),
            "ids": [i for _, i in events],
        })
    out.sort(key=lambda g: (-g["count"], machine_label(g["machine_key"], {}), g["code"]))
    return {
        "groups_30d": sum(1 for g in out if g["window_days"] == 30),
        "groups_90d": sum(1 for g in out if g["window_days"] == 90),
        "groups": out,
    }


def worst_machines(breakdowns: list, orders: list, start: date, end: date, limit: int = 10) -> dict:
    """Machines ranked by breakdowns (then downtime) in the period.

    Open jobs are matched by normalized machine name (breakdowns carry no
    equipment_id yet); the match is documented, not guessed at.
    """
    downtime = machine_downtime(breakdowns, start, end)["rows"]
    open_by_name: dict = {}
    for order in orders:
        if (order.get("status") or "") in TERMINAL_STATUSES:
            continue
        key = (order.get("equipment_info") or "").strip().casefold()
        open_by_name.setdefault(key, []).append(order.get("id"))
    rows = []
    for entry in downtime:
        key = entry["machine"].strip().casefold()
        rows.append({
            "machine": entry["machine"],
            "breakdowns": entry["breakdowns"],
            "downtime_hours": entry["downtime_hours"],
            "open_jobs": len(open_by_name.get(key, [])),
            "breakdown_ids": entry["breakdown_ids"],
            "work_order_ids": open_by_name.get(key, []),
        })
    return {"rows": rows[:limit]}


def cost_by_machine(orders: list, start: date, end: date, equipment_names: dict) -> dict:
    """Per machine: recorded labour hours and spares cost for jobs raised in the period."""
    by_key: dict = {}
    for order in orders:
        raised = parse_date(order.get("date_raised"))
        if raised is None or raised < start or raised > end:
            continue
        key = machine_key(order)
        entry = by_key.setdefault((key[0], str(key[1])), {"key": key, "hours": 0.0, "spares": 0.0, "ids": []})
        entry["hours"] += parse_hours(order.get("total_time_worked"))
        entry["spares"] += spares_cost(order.get("spares_used"))
        entry["ids"].append(order.get("id"))
    rows = [
        {
            "machine": machine_label(entry["key"], equipment_names),
            "equipment_id": entry["key"][1] if entry["key"][0] == "id" else None,
            "labour_hours": round(entry["hours"], 1),
            "spares_cost": round(entry["spares"], 2),
            "jobs": len(entry["ids"]),
            "ids": entry["ids"],
        }
        for entry in by_key.values()
    ]
    rows.sort(key=lambda r: (-r["labour_hours"], -r["spares_cost"], r["machine"]))
    return {"rows": rows}


def compute_measures(
    orders: list,
    breakdowns: list,
    equipment_names: dict,
    today: date,
    days: int = 90,
    weeks: int = 12,
) -> dict:
    """All seven measures over the rows, with the drill-down id lists."""
    start = today - timedelta(days=days)
    repeats = repeat_failures(orders, today)
    for group in repeats["groups"]:
        group["machine"] = machine_label(group.pop("machine_key"), equipment_names)
    return {
        "period": {"days": days, "weeks": weeks, "start": start.isoformat(), "end": today.isoformat()},
        "pm_on_time": pm_on_time(orders, start, today),
        "backlog_by_age": backlog_by_age(orders, today),
        "opened_vs_completed": opened_vs_completed(orders, weeks, today),
        "machine_downtime": machine_downtime(breakdowns, start, today),
        "repeat_failures": repeats,
        "worst_machines": worst_machines(breakdowns, orders, start, today),
        "cost_by_machine": cost_by_machine(orders, start, today, equipment_names),
    }
