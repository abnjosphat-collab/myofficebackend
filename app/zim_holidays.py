# Zimbabwe public holidays — mirrors frontend/lib/zimHolidays.ts for leave-day counting.
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

MUNHUMUTAPA_DAY_FIRST_YEAR = 2026


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


@lru_cache(maxsize=32)
def zim_holidays_for_year(year: int) -> dict[str, str]:
    easter = _easter_sunday(year)
    heroes = _nth_weekday_of_month(year, 8, 0, 2)  # 2nd Monday of August
    holidays = {
        f"{year}-01-01": "New Year's Day",
        f"{year}-02-21": "Robert Gabriel Mugabe National Youth Day",
        (easter - timedelta(days=2)).isoformat(): "Good Friday",
        (easter - timedelta(days=1)).isoformat(): "Easter Saturday",
        (easter + timedelta(days=1)).isoformat(): "Easter Monday",
        f"{year}-04-18": "Independence Day",
        f"{year}-05-01": "Workers' Day",
        f"{year}-05-25": "Africa Day",
        **(
            {f"{year}-09-15": "Munhumutapa Day"}
            if year >= MUNHUMUTAPA_DAY_FIRST_YEAR
            else {}
        ),
        heroes.isoformat(): "Heroes' Day",
        (heroes + timedelta(days=1)).isoformat(): "Defence Forces Day",
        f"{year}-12-22": "Unity Day",
        f"{year}-12-25": "Christmas Day",
        f"{year}-12-26": "Boxing Day",
    }
    return holidays


def zim_holiday_name(date_str: str) -> str | None:
    try:
        year = int(date_str[:4])
    except ValueError:
        return None
    return zim_holidays_for_year(year).get(date_str)
