# tests/test_ai_safety_risk_score.py — the weighted 0-100 SHEQ risk-scoring formula
# behind POST /ai-safety/analyze (analyse(), RISK_WEIGHTS, _risk_level, _trend_direction,
# _date, _split_halves). ai_safety.py was the single lowest-covered module in the
# backend (13%) despite computing the actual safety risk score shown to managers — a
# calculation bug here would misrepresent real safety risk, not just misrender a UI.
# Each RISK_WEIGHTS component is tested in isolation (only that component's inputs
# populated, everything else empty) so a failure points at exactly which weight/cap
# broke, and a combined test locks in the documented invariant that all caps sum to
# exactly 100.

from app.routers.ai_safety import (
    analyse, SafetyDataInput, _risk_level, _trend_direction, _date, _split_halves, _top,
)


def _score(**kwargs) -> int:
    return analyse(SafetyDataInput(**kwargs))["risk_score"]


# ─── Empty input ─────────────────────────────────────────────────────────────────────

def test_empty_input_has_zero_risk_score_and_low_level():
    result = analyse(SafetyDataInput())
    assert result["risk_score"] == 0
    assert result["overall_risk"] == "low"
    assert "No safety records found" in result["summary"]


# ─── Each RISK_WEIGHTS component in isolation ──────────────────────────────────────

def test_near_miss_open_ratio_component():
    # 3 of 5 open = 60% -> int(60 * 0.20) = 12, well under the 20 cap.
    near_miss = [{"status": "open"}] * 3 + [{"status": "closed"}] * 2
    assert _score(near_miss=near_miss) == 12


def test_critical_finding_points_are_capped_at_20():
    # 10 critical findings * 5 points = 50 uncapped, but the cap is 20.
    inspections = [{"findings": [{"priority": "critical"} for _ in range(10)]}]
    assert _score(inspections=inspections) == 20


def test_overdue_finding_points_are_capped_at_20():
    # 10 overdue findings * 4 points = 40 uncapped, capped to 20.
    inspections = [{"findings": [{"status": "overdue"} for _ in range(10)]}]
    assert _score(inspections=inspections) == 20


def test_pending_action_ratio_component():
    # A single VFL record (marked safe, so it doesn't also trigger vfl_risk) carrying
    # 8 pending + 2 done actions -> 80% pending -> int(80 * 0.15) = 12.
    vfl = [{"behaviourCategory": "Safe Act",
            "actions": [{"status": "pending"} for _ in range(8)] + [{"status": "done"} for _ in range(2)]}]
    assert _score(vfl=vfl) == 12


def test_work_stoppage_points_are_capped_at_15():
    # 10 stoppages * 3 points = 30 uncapped, capped to 15.
    work_stoppage = [{} for _ in range(10)]
    assert _score(work_stoppage=work_stoppage) == 15


def test_vfl_unsafe_ratio_component():
    # 100% unsafe -> int(100 * 0.10) = 10, exactly the cap.
    vfl = [{"behaviourCategory": "Unsafe Act"} for _ in range(5)]
    assert _score(vfl=vfl) == 10


# ─── Combined: every cap maxed sums to exactly 100 ─────────────────────────────────

def test_maximally_bad_input_caps_the_total_score_at_100():
    result = analyse(SafetyDataInput(
        near_miss=[{"status": "open"}] * 20,
        inspections=[{"findings":
            [{"priority": "critical"} for _ in range(10)] +
            [{"status": "overdue"} for _ in range(10)]
        }],
        work_stoppage=[{"correctiveActions": [{"status": "pending"}]} for _ in range(10)],
        vfl=[{"behaviourCategory": "Unsafe Act"} for _ in range(5)],
    ))
    # RISK_WEIGHTS' own caps (20+20+20+15+15+10) sum to 100 "by design" per its
    # comment — this is the test that actually proves that invariant holds.
    assert result["risk_score"] == 100
    assert result["overall_risk"] == "critical"


# ─── _risk_level — classification boundaries ───────────────────────────────────────

def test_risk_level_boundaries():
    assert _risk_level(0) == "low"
    assert _risk_level(29) == "low"
    assert _risk_level(30) == "medium"
    assert _risk_level(49) == "medium"
    assert _risk_level(50) == "high"
    assert _risk_level(69) == "high"
    assert _risk_level(70) == "critical"
    assert _risk_level(100) == "critical"


# ─── _trend_direction ───────────────────────────────────────────────────────────────

def test_trend_both_zero_is_stable():
    assert _trend_direction(0, 0) == "stable"


def test_trend_from_zero_baseline_is_worsening():
    assert _trend_direction(0, 5) == "worsening"


def test_trend_exactly_20pct_is_stable_not_worsening():
    assert _trend_direction(10, 12) == "stable"


def test_trend_over_20pct_increase_is_worsening():
    assert _trend_direction(10, 13) == "worsening"


def test_trend_over_20pct_decrease_is_improving():
    assert _trend_direction(10, 7) == "improving"


# ─── _date — multi-field-name parsing, Z-suffix handling ──────────────────────────

def test_date_prefers_date_field_first():
    d = _date({"date": "2024-03-15", "created_at": "2024-01-01"})
    assert d.year == 2024 and d.month == 3 and d.day == 15


def test_date_falls_back_through_field_names_in_order():
    d = _date({"submittedAt": "2024-05-20T10:00:00"})
    assert d.year == 2024 and d.month == 5 and d.day == 20


def test_date_handles_z_suffix_utc_timestamps():
    d = _date({"createdAt": "2024-06-01T00:00:00Z"})
    assert d is not None and d.year == 2024 and d.month == 6 and d.day == 1


def test_date_missing_all_fields_is_none():
    assert _date({}) is None


def test_date_malformed_value_is_none_not_a_crash():
    assert _date({"date": "not-a-date"}) is None


# ─── _split_halves ───────────────────────────────────────────────────────────────────

def test_split_halves_orders_by_date():
    records = [{"id": "late", "date": "2024-03-01"}, {"id": "early", "date": "2024-01-01"}]
    older, newer = _split_halves(records)
    assert [r["id"] for r in older] == ["early"]
    assert [r["id"] for r in newer] == ["late"]


def test_split_halves_empty_input():
    assert _split_halves([]) == ([], [])


# ─── _top ─────────────────────────────────────────────────────────────────────────

def test_top_ranks_by_count_descending():
    records = [{"location": "Bay 1"}, {"location": "Bay 1"}, {"location": "Bay 2"}]
    result = _top(records, "location", n=5)
    assert result[0] == {"value": "Bay 1", "count": 2}
    assert result[1] == {"value": "Bay 2", "count": 1}


def test_top_ignores_blank_values():
    records = [{"location": ""}, {"location": None}, {"location": "Bay 1"}]
    result = _top(records, "location")
    assert result == [{"value": "Bay 1", "count": 1}]


def test_top_respects_n_limit():
    records = [{"location": f"Bay {i}"} for i in range(10)]
    assert len(_top(records, "location", n=3)) == 3
