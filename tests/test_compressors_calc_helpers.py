# tests/test_compressors_calc_helpers.py — the pure calc functions behind compressor
# maintenance tracking (calculate_efficiency, get_service_urgency,
# generate_service_intervals, calculate_daily_hours, validate_date_format,
# ensure_valid_daily_hours). compressors.py was 24% covered with no test file at all;
# these six are DB-free and drive real maintenance decisions (when a compressor's next
# service is due, how urgently) — aimed at the actual failure modes (div-by-zero,
# reversed meter readings, out-of-range loaded-vs-running hours) rather than
# happy-path-only coverage.

from app.routers.compressors import (
    calculate_efficiency, get_service_urgency, generate_service_intervals,
    calculate_daily_hours, validate_date_format, ensure_valid_daily_hours, ServiceUrgency,
)


# ─── calculate_efficiency ───────────────────────────────────────────────────────────

def test_efficiency_normal_case():
    assert calculate_efficiency(10, 8) == 80.0


def test_efficiency_zero_running_hours_is_zero_not_a_divide_by_zero_crash():
    assert calculate_efficiency(0, 0) == 0.0


def test_efficiency_rounds_to_one_decimal():
    assert calculate_efficiency(3, 1) == 33.3


def test_efficiency_is_capped_at_100_percent():
    # loaded > running shouldn't normally happen, but a bad reading must not report
    # an efficiency above 100%.
    assert calculate_efficiency(10, 15) == 100.0


# ─── get_service_urgency ────────────────────────────────────────────────────────────

def test_urgency_overdue_is_critical():
    assert get_service_urgency(-10) == ServiceUrgency.CRITICAL


def test_urgency_due_now_is_critical():
    assert get_service_urgency(0) == ServiceUrgency.CRITICAL


def test_urgency_within_7_days_is_high():
    assert get_service_urgency(8, avg_daily_hours=8.0) == ServiceUrgency.HIGH  # 1 day


def test_urgency_exactly_7_days_is_high_not_medium():
    assert get_service_urgency(56, avg_daily_hours=8.0) == ServiceUrgency.HIGH  # 7 days


def test_urgency_8_days_is_medium_not_high():
    assert get_service_urgency(64, avg_daily_hours=8.0) == ServiceUrgency.MEDIUM  # 8 days


def test_urgency_exactly_30_days_is_medium_not_low():
    assert get_service_urgency(240, avg_daily_hours=8.0) == ServiceUrgency.MEDIUM  # 30 days


def test_urgency_31_days_is_low():
    assert get_service_urgency(248, avg_daily_hours=8.0) == ServiceUrgency.LOW  # 31 days


def test_urgency_respects_custom_daily_hours():
    assert get_service_urgency(100, avg_daily_hours=10.0) == ServiceUrgency.MEDIUM  # 10 days


# ─── generate_service_intervals ─────────────────────────────────────────────────────

def test_intervals_from_zero_hours_returns_all():
    assert generate_service_intervals(0) == [1000, 2000, 4000, 8000, 16000]


def test_intervals_excludes_already_passed():
    assert generate_service_intervals(1500) == [2000, 4000, 8000, 16000]


def test_intervals_at_last_milestone_is_empty():
    assert generate_service_intervals(16000) == []


def test_intervals_past_last_milestone_is_empty():
    assert generate_service_intervals(20000) == []


# ─── calculate_daily_hours ──────────────────────────────────────────────────────────

def test_daily_hours_normal_case():
    assert calculate_daily_hours(100, 108) == 8.0


def test_daily_hours_no_change():
    assert calculate_daily_hours(100, 100) == 0.0


def test_daily_hours_reversed_meter_reading_clamps_to_zero():
    # A meter that reads lower than the previous reading (reset, entry error) must not
    # produce a negative daily-hours figure.
    assert calculate_daily_hours(100, 95) == 0.0


def test_daily_hours_rounds_to_two_decimals():
    assert calculate_daily_hours(100.111, 108.3339) == 8.22


# ─── validate_date_format ───────────────────────────────────────────────────────────

def test_valid_date_accepted():
    assert validate_date_format("2024-01-15") is True


def test_invalid_month_rejected():
    assert validate_date_format("2024-13-01") is False


def test_nonexistent_day_rejected():
    assert validate_date_format("2024-02-30") is False


def test_non_date_string_rejected():
    assert validate_date_format("not-a-date") is False


def test_empty_string_rejected():
    assert validate_date_format("") is False


# ─── ensure_valid_daily_hours ───────────────────────────────────────────────────────

def test_ensure_valid_normal_case_recomputes_total():
    running, loaded, total = ensure_valid_daily_hours(
        daily_running=8, daily_loaded=6, current_total_loaded=999, previous_total_loaded=100)
    assert (running, loaded, total) == (8, 6, 106)


def test_ensure_valid_clamps_loaded_to_running():
    # A logged/estimated loaded-hours figure can't exceed the running hours it's a
    # subset of.
    running, loaded, total = ensure_valid_daily_hours(
        daily_running=5, daily_loaded=8, current_total_loaded=0, previous_total_loaded=50)
    assert (running, loaded, total) == (5, 5, 55)


def test_ensure_valid_negative_running_hours_floors_both_to_zero():
    # daily_loaded is first clamped against the (still-negative) daily_running, THEN
    # both are floored to 0 — documents the actual clamp order, since floor-then-clamp
    # would give a different (wrong) result here.
    running, loaded, total = ensure_valid_daily_hours(
        daily_running=-3, daily_loaded=2, current_total_loaded=0, previous_total_loaded=10)
    assert (running, loaded, total) == (0, 0, 10)
