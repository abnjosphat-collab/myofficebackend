"""NEC payroll period: previous month 13th through payroll month 12th."""
from datetime import date
from typing import List, Tuple


def nec_period_for_payroll_month(year: int, month: int) -> Tuple[date, date]:
    """Payroll month is the calendar month containing the period end (12th)."""
    if month < 1 or month > 12:
        raise ValueError("month must be 1–12")
    end = date(year, month, 12)
    if month == 1:
        start = date(year - 1, 12, 13)
    else:
        start = date(year, month - 1, 13)
    return start, end


def iter_period_dates(start: date, end: date) -> List[str]:
    out: List[str] = []
    d = start
    while d <= end:
        out.append(d.isoformat())
        d = date.fromordinal(d.toordinal() + 1)
    return out
