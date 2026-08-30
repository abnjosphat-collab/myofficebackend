# tests/test_aggregation.py — direct unit tests for count_by/sum_by, the shared
# grouping helpers used across 16 routers (per this module's own docstring). Had
# incidental coverage only, via whichever router stats tests happened to call
# count_by with a field-name key — sum_by and the key-function (not just field-name)
# call form had no coverage at all.

from app.aggregation import count_by, sum_by


# ─── count_by ────────────────────────────────────────────────────────────────────────

def test_count_by_field_name():
    records = [{"status": "open"}, {"status": "open"}, {"status": "closed"}]
    assert count_by(records, "status") == {"open": 2, "closed": 1}


def test_count_by_missing_field_uses_default():
    records = [{"status": "open"}, {}]
    assert count_by(records, "status") == {"open": 1, "unknown": 1}


def test_count_by_custom_default():
    records = [{}]
    assert count_by(records, "status", default="N/A") == {"N/A": 1}


def test_count_by_key_function():
    records = [{"a": "x", "b": None}, {"a": None, "b": "y"}]
    result = count_by(records, lambda r: r["a"] or r["b"])
    assert result == {"x": 1, "y": 1}


def test_count_by_empty_records_is_empty_dict():
    assert count_by([], "status") == {}


def test_count_by_returns_a_plain_dict_not_counter():
    result = count_by([{"status": "open"}], "status")
    assert type(result) is dict


# ─── sum_by ──────────────────────────────────────────────────────────────────────────

def test_sum_by_totals_a_numeric_field_per_group():
    records = [
        {"department": "Mechanical", "downtime_minutes": 30},
        {"department": "Mechanical", "downtime_minutes": 45},
        {"department": "Electrical", "downtime_minutes": 10},
    ]
    result = sum_by(records, "department", "downtime_minutes")
    assert result == {"Mechanical": 75, "Electrical": 10}


def test_sum_by_treats_none_value_as_zero():
    records = [{"department": "Mechanical", "downtime_minutes": None}, {"department": "Mechanical", "downtime_minutes": 20}]
    result = sum_by(records, "department", "downtime_minutes")
    assert result["Mechanical"] == 20


def test_sum_by_missing_value_field_defaults_to_zero():
    records = [{"department": "Mechanical"}]
    result = sum_by(records, "department", "downtime_minutes")
    assert result["Mechanical"] == 0


def test_sum_by_key_function_and_value_function():
    records = [{"a": "x", "hours": 5}, {"a": "x", "hours": 3}]
    result = sum_by(records, lambda r: r["a"], lambda r: r["hours"])
    assert result == {"x": 8}


def test_sum_by_empty_records_is_empty_dict():
    assert sum_by([], "department", "downtime_minutes") == {}
