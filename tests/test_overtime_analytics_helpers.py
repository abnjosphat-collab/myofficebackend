# tests/test_overtime_analytics_helpers.py — the pure calc functions behind
# overtime.py's analytics engine (_hours, _categorize, _weekday/_start_hour,
# _trend_direction, _iso_week_key, _group_hours, _split_halves). These are real
# payroll-adjacent logic (hours computation feeds directly into what's reported as
# overtime worked; trend/category signals are what a manager acts on) with zero prior
# test coverage — overtime.py was 27% covered, the lowest of any payroll module, and
# nothing here touched the DB or FastAPI at all, so there's no excuse for it having been
# untested. Aimed at the actual failure modes (overnight-shift wraparound, malformed
# input, ISO week year-boundary rollover, the exact 20% trend threshold) rather than
# happy-path-only coverage.

from datetime import datetime

from app.routers.overtime import (
    _hours, _categorize, _weekday, _start_hour, _trend_direction,
    _iso_week_key, _group_hours, _split_halves, _date,
)


# ─── _hours — the actual pay-relevant duration calculation ────────────────────────

def test_hours_field_takes_priority_over_times():
    assert _hours({"hours": 3.5, "start_time": "08:00", "end_time": "09:00"}) == 3.5


def test_hours_field_coerces_numeric_strings():
    assert _hours({"hours": "4"}) == 4.0


def test_hours_field_non_numeric_falls_back_to_times():
    # "abc" can't become a float — falls through to start/end, which are absent here,
    # so the correct answer is 0.0, not a crash.
    assert _hours({"hours": "abc"}) == 0.0


def test_normal_shift_computed_from_times():
    assert _hours({"start_time": "17:00", "end_time": "19:00"}) == 2.0


def test_partial_hour_shift():
    assert _hours({"start_time": "17:30", "end_time": "19:45"}) == 2.25


def test_overnight_shift_wraps_past_midnight():
    # 22:00 -> 02:00 is 4 hours worked, not a negative duration.
    assert _hours({"start_time": "22:00", "end_time": "02:00"}) == 4.0


def test_missing_end_time_is_zero():
    assert _hours({"start_time": "17:00", "end_time": None}) == 0.0


def test_missing_both_is_zero():
    assert _hours({}) == 0.0


def test_malformed_time_string_is_zero_not_a_crash():
    assert _hours({"start_time": "17:00", "end_time": "garbage"}) == 0.0


# ─── _categorize — known recurring-task bucketing ──────────────────────────────────

def test_categorize_matches_known_phrase():
    assert _categorize("Daily checks on the winder") == "Daily Checks"


def test_categorize_is_case_insensitive():
    assert _categorize("DAILY CHECK on machine 3") == "Daily Checks"


def test_categorize_different_known_phrases_map_to_same_label():
    # "daily check" and "southwell daily checks" are the same routine task and must
    # collapse to one label, not fragment into two separate reported causes.
    assert _categorize("Winder daily checks") == "Daily Checks"
    assert _categorize("Southwell daily checks") == "Daily Checks"


def test_categorize_unmatched_reason_is_none():
    assert _categorize("Random unrelated reason text") is None


# ─── _date / _weekday / _start_hour — input parsing edge cases ────────────────────

def test_weekday_parses_iso_date():
    assert _weekday({"date": "2024-01-01"}) == 0  # Monday


def test_weekday_missing_date_is_none():
    assert _weekday({"date": None}) is None


def test_weekday_malformed_date_is_none_not_a_crash():
    assert _weekday({"date": "not-a-date"}) is None


def test_date_accepts_timestamp_prefix():
    # Records may carry a full timestamp; only the date portion should be parsed.
    assert _date({"date": "2024-03-15T08:30:00Z"}) == datetime(2024, 3, 15)


def test_start_hour_parses_leading_hour():
    assert _start_hour({"start_time": "17:30"}) == 17


def test_start_hour_missing_is_none():
    assert _start_hour({"start_time": None}) is None


def test_start_hour_out_of_range_wraps_modulo_24():
    # No validation on the raw string — documents the actual (surprising) behavior
    # rather than assuming it's clamped or rejected.
    assert _start_hour({"start_time": "25:00"}) == 1


def test_start_hour_malformed_is_none_not_a_crash():
    assert _start_hour({"start_time": "garbage"}) is None


# ─── _trend_direction — the worsening/improving/stable signal shown to managers ────

def test_trend_both_zero_is_stable():
    assert _trend_direction(0, 0) == "stable"


def test_trend_from_zero_baseline_is_worsening():
    assert _trend_direction(0, 5) == "worsening"


def test_trend_increase_over_20pct_is_worsening():
    assert _trend_direction(10, 13) == "worsening"  # +30%


def test_trend_exactly_20pct_increase_is_stable_not_worsening():
    # Boundary: the check is `> 0.20`, so exactly 20% must NOT trip it.
    assert _trend_direction(10, 12) == "stable"


def test_trend_decrease_over_20pct_is_improving():
    assert _trend_direction(10, 7) == "improving"  # -30%


def test_trend_exactly_20pct_decrease_is_stable_not_improving():
    assert _trend_direction(10, 8) == "stable"


def test_trend_small_baseline_uses_floor_of_1_in_denominator():
    # max(old_val, 1) guards against a tiny old_val exaggerating the ratio — without
    # it, 0.5 -> 2 would compute as +300% instead of the intended +150%. Either way
    # it's "worsening" here, but the floor changes the case right at the threshold.
    assert _trend_direction(0.5, 0.65) == "stable"  # (0.65-0.5)/1 = 0.15, not /0.5 = 0.30


# ─── _iso_week_key — year-boundary rollover, the classic off-by-one-year bug ───────

def test_iso_week_key_mid_year():
    assert _iso_week_key(datetime(2024, 1, 1)) == "2024-W01"


def test_iso_week_key_new_years_day_belongs_to_prior_iso_year():
    # Jan 1 2023 was a Sunday — under ISO 8601 that week's Thursday falls in 2022, so
    # this date belongs to ISO week 52 of 2022, not "week 1 of 2023". A naive
    # `f"{d.year}-W{d.isocalendar()[1]}"` (using d.year instead of isocalendar()'s own
    # year) would get this wrong.
    assert _iso_week_key(datetime(2023, 1, 1)) == "2022-W52"


def test_iso_week_key_dec_31_can_belong_to_next_iso_year():
    # The mirror case: the last calendar day of 2024 already falls in ISO week 1 of 2025.
    assert _iso_week_key(datetime(2024, 12, 31)) == "2025-W01"


# ─── _group_hours — per-key hour totals (pure-Python fallback path) ───────────────

def test_group_hours_sums_per_key():
    records = [
        {"department": "Mechanical", "hours": 2},
        {"department": "Mechanical", "hours": 3},
        {"department": "Electrical", "hours": 1.5},
    ]
    result = _group_hours(records, lambda r: r.get("department"))
    assert result == {"Mechanical": 5.0, "Electrical": 1.5}


def test_group_hours_falsy_key_buckets_as_unassigned():
    records = [{"department": None, "hours": 4}, {"department": "", "hours": 1}]
    result = _group_hours(records, lambda r: r.get("department"))
    assert result == {"Unassigned": 5.0}


def test_group_hours_empty_list():
    assert _group_hours([], lambda r: r.get("department")) == {}


# ─── _split_halves — older/newer comparison set for trend detection ───────────────

def test_split_halves_orders_by_date_not_input_order():
    records = [
        {"id": "late", "date": "2024-03-01"},
        {"id": "early", "date": "2024-01-01"},
        {"id": "mid", "date": "2024-02-01"},
    ]
    older, newer = _split_halves(records)
    assert [r["id"] for r in older] == ["early"]
    assert [r["id"] for r in newer] == ["mid", "late"]


def test_split_halves_drops_records_with_unparseable_dates():
    records = [{"id": "ok", "date": "2024-01-01"}, {"id": "bad", "date": None}]
    older, newer = _split_halves(records)
    all_ids = {r["id"] for r in older} | {r["id"] for r in newer}
    assert "bad" not in all_ids
    assert "ok" in all_ids


def test_split_halves_empty_input():
    assert _split_halves([]) == ([], [])
