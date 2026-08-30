# tests/test_breakdowns_time_metrics.py — time_to_minutes, calculate_time_metrics, and
# calculate_spare_costs compute the response-time/repair-time/downtime SLA figures and
# spare-part cost totals for every breakdown record, with zero prior dedicated tests
# (test_breakdowns_heatmap.py only covers the heatmap endpoint itself).

from app.routers.breakdowns import time_to_minutes, calculate_time_metrics, calculate_spare_costs, SparePart


# ─── time_to_minutes ─────────────────────────────────────────────────────────────────

def test_parses_hh_mm():
    assert time_to_minutes("08:30") == 510


def test_parses_hh_mm_ss_ignoring_seconds():
    assert time_to_minutes("08:30:15") == 510


def test_none_is_zero():
    assert time_to_minutes(None) == 0


def test_empty_string_is_zero():
    assert time_to_minutes("") == 0


def test_malformed_string_is_zero_not_a_crash():
    assert time_to_minutes("garbage") == 0


# ─── calculate_time_metrics ─────────────────────────────────────────────────────────

def test_normal_breakdown_computes_all_three_metrics():
    data = {
        "breakdown_start": "08:00", "breakdown_end": "10:00",
        "work_start": "08:30", "work_end": "09:30",
    }
    result = calculate_time_metrics(data)
    assert result["response_time_minutes"] == 30    # 08:30 - 08:00
    assert result["repair_time_minutes"] == 60       # 09:30 - 08:30
    assert result["downtime_minutes"] == 120         # 10:00 - 08:00
    assert result["net_downtime_minutes"] == 120


def test_missing_work_times_gives_zero_response_and_repair():
    data = {"breakdown_start": "08:00", "breakdown_end": "10:00"}
    result = calculate_time_metrics(data)
    assert result["response_time_minutes"] == 0
    assert result["repair_time_minutes"] == 0
    assert result["downtime_minutes"] == 120


def test_missing_all_fields_is_all_zero():
    result = calculate_time_metrics({})
    assert result == {
        "response_time_minutes": 0, "repair_time_minutes": 0,
        "downtime_minutes": 0, "net_downtime_minutes": 0,
    }


def test_negative_duration_is_clamped_to_zero():
    # work_start logged before breakdown_start (a data-entry error) must not report a
    # negative response time.
    data = {"breakdown_start": "10:00", "work_start": "08:00"}
    result = calculate_time_metrics(data)
    assert result["response_time_minutes"] == 0


def test_breakdown_at_exactly_midnight_reports_zero_response_time():
    # Real quirk found while writing these tests, not fixed here (narrow edge case,
    # not confirmed to have a live consequence): `if b_start and w_start` treats a
    # midnight breakdown_start ("00:00" -> time_to_minutes 0) as falsy, so
    # response_time/downtime report 0 regardless of the actual work timings, instead
    # of computing the real elapsed time. Documented so this doesn't get "fixed" by
    # accident as a side effect of an unrelated change without someone deciding on
    # purpose whether to change the comparison to `is not None`.
    data = {"breakdown_start": "00:00", "breakdown_end": "02:00", "work_start": "00:30", "work_end": "01:30"}
    result = calculate_time_metrics(data)
    assert result["response_time_minutes"] == 0  # "should" arguably be 30
    assert result["downtime_minutes"] == 0        # "should" arguably be 120


# ─── calculate_spare_costs ──────────────────────────────────────────────────────────

def test_computes_total_cost_across_spares():
    spares = [
        SparePart(name="Bearing", quantity=2, unit_price=15.0),
        SparePart(name="Seal", quantity=1, unit_price=8.5),
    ]
    result = calculate_spare_costs(spares)
    assert result["total_spare_cost"] == 38.5  # (2*15.0) + (1*8.5)


def test_recomputes_per_item_total_cost_regardless_of_input_value():
    # total_cost is always recalculated from quantity*unit_price, not trusted from
    # whatever was passed in on the model.
    spares = [SparePart(name="Filter", quantity=3, unit_price=4.0, total_cost=999.0)]
    result = calculate_spare_costs(spares)
    assert result["spares_used"][0]["total_cost"] == 12.0


def test_empty_spares_list_is_zero_cost():
    result = calculate_spare_costs([])
    assert result == {"total_spare_cost": 0.0, "spares_used": []}


def test_rounds_total_cost_to_two_decimals():
    spares = [SparePart(name="Widget", quantity=3, unit_price=3.333)]
    result = calculate_spare_costs(spares)
    assert result["total_spare_cost"] == 10.0  # 3 * 3.333 = 9.999 -> 10.0
