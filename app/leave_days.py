# app/leave_days.py — inclusive leave duration with optional working-day mode.
from __future__ import annotations

from datetime import date, timedelta

from app.zim_holidays import zim_holiday_name


def calculate_calendar_days(start: date, end: date) -> int:
    return (end - start).days + 1


def is_working_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return zim_holiday_name(d.isoformat()) is None


def calculate_working_days(start: date, end: date) -> int:
    count = 0
    cur = start
    while cur <= end:
        if is_working_day(cur):
            count += 1
        cur += timedelta(days=1)
    return count


def calculate_total_days(
    start: date,
    end: date,
    exclude_weekends_holidays: bool = False,
) -> int:
    if exclude_weekends_holidays:
        return calculate_working_days(start, end)
    return calculate_calendar_days(start, end)
