# tests/test_schedules_recurrence.py — the recurrence-calculation engine behind
# recurring maintenance schedules (_clamp_dom, next_occurrence, _as_dates, is_due).
# schedules.py's own top comment explains why this matters: schedules used to live in
# browser localStorage, invisible to everyone but their creator, and "a work order that
# only gets raised if somebody happens to open the page is not a schedule" - this is the
# server-side cron-chain logic that replaced that. Zero prior tests despite being real
# date-math with several classic failure modes (month-end clamping across a leap year,
# December->January year rollover, quarterly wraparound).

from datetime import date

from app.routers.schedules import _clamp_dom, next_occurrence, _as_dates, is_due, FAR_FUTURE


# ─── _clamp_dom ──────────────────────────────────────────────────────────────────────

def test_clamp_dom_normal_day_is_unchanged():
    assert _clamp_dom(2024, 3, 15) == date(2024, 3, 15)


def test_clamp_dom_31st_clamps_to_28_in_non_leap_february():
    assert _clamp_dom(2023, 2, 31) == date(2023, 2, 28)


def test_clamp_dom_31st_clamps_to_29_in_leap_february():
    assert _clamp_dom(2024, 2, 31) == date(2024, 2, 29)


def test_clamp_dom_31st_clamps_to_30_in_a_30_day_month():
    assert _clamp_dom(2024, 4, 31) == date(2024, 4, 30)


def test_clamp_dom_handles_month_rolling_past_december():
    # month=13 must roll into January of the following year, not raise.
    assert _clamp_dom(2024, 13, 15) == date(2025, 1, 15)


# ─── next_occurrence ─────────────────────────────────────────────────────────────────

def test_daily_adds_one_day():
    s = {"recurrence_type": "daily"}
    assert next_occurrence(s, date(2024, 6, 10)) == date(2024, 6, 11)


def test_weekly_adds_seven_days():
    s = {"recurrence_type": "weekly"}
    assert next_occurrence(s, date(2024, 6, 10)) == date(2024, 6, 17)


def test_biweekly_adds_fourteen_days():
    s = {"recurrence_type": "biweekly"}
    assert next_occurrence(s, date(2024, 6, 10)) == date(2024, 6, 24)


def test_monthly_advances_one_month_same_day():
    s = {"recurrence_type": "monthly", "recurrence_dom": 15}
    assert next_occurrence(s, date(2024, 6, 10)) == date(2024, 7, 15)


def test_monthly_rolls_over_the_year_boundary():
    s = {"recurrence_type": "monthly", "recurrence_dom": 5}
    assert next_occurrence(s, date(2024, 12, 10)) == date(2025, 1, 5)


def test_monthly_clamps_day_31_into_a_30_day_month():
    s = {"recurrence_type": "monthly", "recurrence_dom": 31}
    # From Jan 31 -> next month is Feb, which has no 31st.
    assert next_occurrence(s, date(2024, 1, 31)) == date(2024, 2, 29)  # 2024 is a leap year


def test_quarterly_picks_the_next_month_in_the_list():
    s = {"recurrence_type": "quarterly", "recurrence_months": [0, 3, 6, 9], "recurrence_dom": 1}
    # Feb (0-indexed month=1) -> next listed month after that is April (index 3).
    assert next_occurrence(s, date(2024, 2, 15)) == date(2024, 4, 1)


def test_quarterly_wraps_to_next_year_when_past_the_last_listed_month():
    s = {"recurrence_type": "quarterly", "recurrence_months": [0, 3, 6, 9], "recurrence_dom": 1}
    # November (0-indexed month=10) is past every listed month -> wraps to Jan next year.
    assert next_occurrence(s, date(2024, 11, 15)) == date(2025, 1, 1)


def test_quarterly_defaults_to_standard_quarters_when_months_not_set():
    s = {"recurrence_type": "quarterly", "recurrence_dom": 1}
    assert next_occurrence(s, date(2024, 2, 15)) == date(2024, 4, 1)


def test_yearly_advances_to_next_year_same_configured_month():
    s = {"recurrence_type": "yearly", "recurrence_months": [5], "recurrence_dom": 10}  # June (index 5)
    assert next_occurrence(s, date(2024, 3, 1)) == date(2025, 6, 10)


def test_custom_picks_the_next_specific_date_strictly_after_frm():
    s = {"recurrence_type": "custom", "specific_dates": ["2024-05-01", "2024-08-15", "2024-03-01"]}
    assert next_occurrence(s, date(2024, 5, 1)) == date(2024, 8, 15)  # 2024-05-01 itself excluded (must be strictly after)


def test_custom_with_no_future_dates_returns_far_future():
    s = {"recurrence_type": "custom", "specific_dates": ["2020-01-01"]}
    assert next_occurrence(s, date(2024, 1, 1)) == FAR_FUTURE


def test_unrecognized_recurrence_type_falls_back_to_far_future():
    assert next_occurrence({"recurrence_type": "bogus"}, date(2024, 1, 1)) == FAR_FUTURE


# ─── _as_dates ───────────────────────────────────────────────────────────────────────

def test_as_dates_parses_iso_strings():
    assert _as_dates(["2024-01-15", "2024-02-20"]) == [date(2024, 1, 15), date(2024, 2, 20)]


def test_as_dates_passes_through_real_date_objects():
    d = date(2024, 3, 1)
    assert _as_dates([d]) == [d]


def test_as_dates_skips_unparseable_entries_instead_of_crashing():
    assert _as_dates(["2024-01-15", "not-a-date"]) == [date(2024, 1, 15)]


def test_as_dates_none_input_is_empty():
    assert _as_dates(None) == []


# ─── is_due ──────────────────────────────────────────────────────────────────────────

def test_inactive_schedule_is_never_due():
    s = {"active": False, "next_due_date": date(2020, 1, 1)}
    assert is_due(s, date(2024, 1, 1)) is False


def test_no_next_due_date_is_not_due():
    s = {"active": True}
    assert is_due(s, date(2024, 1, 1)) is False


def test_overdue_schedule_is_due():
    s = {"active": True, "next_due_date": date(2024, 1, 1)}
    assert is_due(s, date(2024, 6, 1)) is True


def test_due_date_exactly_today_is_due():
    s = {"active": True, "next_due_date": date(2024, 6, 1)}
    assert is_due(s, date(2024, 6, 1)) is True


def test_future_due_date_with_no_advance_notice_is_not_due():
    s = {"active": True, "next_due_date": date(2024, 6, 10), "advance_days": 0}
    assert is_due(s, date(2024, 6, 5)) is False


def test_advance_days_pulls_the_due_check_earlier():
    s = {"active": True, "next_due_date": date(2024, 6, 10), "advance_days": 5}
    assert is_due(s, date(2024, 6, 5)) is True   # exactly 5 days early
    assert is_due(s, date(2024, 6, 4)) is False  # one day too early


def test_next_due_date_as_iso_string_is_parsed():
    s = {"active": True, "next_due_date": "2024-01-01"}
    assert is_due(s, date(2024, 6, 1)) is True
