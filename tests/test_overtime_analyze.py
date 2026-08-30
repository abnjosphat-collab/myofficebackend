# tests/test_overtime_analyze.py — _analyze_overtime / analyze_overtime, the ~270-line
# analytics orchestrator that COMPOSES the already-unit-tested pure helpers (_hours,
# _categorize, _group_hours, _split_halves, _trend_direction, _iso_week_key — see
# test_overtime_analytics_helpers.py) into the full report the frontend renders. Zero
# prior coverage of the composition itself: it was possible for every helper to be
# individually correct while the orchestrator still wired them together wrong (a wrong
# threshold, a dropped section, a miscomputed percentage) with nothing to catch it.
#
# Fixture design note: reason text that would tie under `_analyze_overtime`'s bigram/
# trigram frequency counting (phrase_count is built by iterating a Python `set`, whose
# iteration order is not guaranteed stable across interpreter runs when two phrases have
# equal count/hours) is deliberately avoided — every non-KNOWN_CATEGORIES reason below
# is unique text so it can never reach the `count >= 2` threshold and enter top_reasons
# in the first place. This keeps every assertion below deterministic rather than
# accidentally coupled to hash-seed-dependent tie-breaking.

import pytest
from fastapi import HTTPException

from app.routers import overtime as overtime_mod
from app.routers.overtime import (
    OvertimeAnalysisInput, _analyze_overtime, analyze_overtime, _equipment_names, _group_hours,
)


@pytest.fixture(autouse=True)
def no_network_equipment_lookup(monkeypatch):
    """_equipment_names() hits the live `equipment` table for machine cross-referencing.
    Default it to empty (best-effort, same as a lookup failure) so analyze tests never
    touch the network; individual tests override via monkeypatch when they want to
    assert on top_machines specifically."""
    monkeypatch.setattr(overtime_mod, "_equipment_names", lambda: [])


def _rec(employee, dept, date, reason, otype="regular", **kw):
    row = {
        "employee_name": employee, "department": dept, "date": date,
        "overtime_type": otype, "reason": reason,
    }
    row.update(kw)
    return row


# ─── A realistic multi-employee, multi-week fixture exercising most branches at once ──

def _build_fixture():
    records = []
    # Alice — "Daily Checks" (KNOWN_CATEGORIES), Processing, Mondays, 7 instances @ 2h.
    for i, d in enumerate(["2024-01-01", "2024-01-08", "2024-01-15", "2024-01-22",
                            "2024-01-29", "2024-02-05", "2024-02-12"]):
        records.append(_rec(
            "Alice Smith", "Processing", d, "Winder daily checks after shift",
            otype="regular" if i % 2 == 0 else "weekend",
            start_time="17:00", end_time="19:00",
            spares_used=[{"name": "Grease Cartridge"}] if i < 4 else [],
        ))
    # Bob — "Loco Breakdown" (KNOWN_CATEGORIES), Processing, Wednesdays, 4 instances @ 3h.
    for i, d in enumerate(["2024-01-03", "2024-01-17", "2024-01-31", "2024-02-14"]):
        records.append(_rec(
            "Bob Jones", "Processing", d, "Loco breakdown near shaft 5, winder affected",
            otype="holiday" if i == 0 else "regular",
            start_time="20:00", end_time="23:00",
            spares_used=[{"name": "Brake Pad"}],
        ))
    # Carol — two singleton (never-repeating) reasons, Engineering, light load.
    records.append(_rec("Carol White", "Engineering", "2024-01-05", "Site pump inspection requested", hours=1.5))
    records.append(_rec("Carol White", "Engineering", "2024-02-20", "Cable fault troubleshooting shift", hours=1.5))
    return records


def test_analyze_overtime_totals_and_averages():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))

    assert result["total_instances"] == 13
    assert result["total_hours"] == 29.0  # 7*2 + 4*3 + 2*1.5
    assert result["employees_involved"] == 3
    assert result["sections_involved"] == 2  # Processing, Engineering ('Unassigned' excluded when present)
    assert result["avg_hours_per_instance"] == round(29.0 / 13, 1)
    assert result["avg_hours_per_employee"] == round(29.0 / 3, 1)


def test_analyze_overtime_top_employees_and_sections_ranked_by_hours():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))

    assert result["top_employees"] == [
        {"name": "Alice Smith", "hours": 14.0},
        {"name": "Bob Jones", "hours": 12.0},
        {"name": "Carol White", "hours": 3.0},
    ]
    assert result["top_sections"] == [
        {"section": "Processing", "hours": 26.0},
        {"section": "Engineering", "hours": 3.0},
    ]


def test_analyze_overtime_double_time_pct_from_weekend_and_holiday():
    # Alice: 3 weekend records @ 2h = 6h. Bob: 1 holiday record @ 3h = 3h. Total 2.0x = 9h.
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))
    assert result["double_time_pct"] == round(9 / 29 * 100)


def test_analyze_overtime_categorized_reasons_become_top_reasons():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))

    # Carol's two reasons never repeat (count 1 each), so only the two KNOWN_CATEGORIES
    # phrases clear the `count >= 2` threshold — this list is fully deterministic.
    assert result["top_reasons"] == [
        {"phrase": "Daily Checks", "count": 7, "hours": 14.0},
        {"phrase": "Loco Breakdown", "count": 4, "hours": 12.0},
    ]


def test_analyze_overtime_bigram_collapses_into_containing_trigram():
    # A repeated 3-word (post-stopword) reason produces one trigram plus two bigrams
    # that are pure substrings of it, derived from the exact same occurrences (same
    # count). The dedup step must collapse the shorter phrases into the trigram rather
    # than reporting three near-duplicate "recurring causes" for one real cause.
    records = [
        _rec(f"Emp{i}", "Ops", "2024-01-0" + str(i + 1), "Rope change urgent")
        for i in range(3)
    ]
    records = [dict(r, hours=1.0) for r in records]

    result = _analyze_overtime(OvertimeAnalysisInput(records=records))

    assert result["top_reasons"] == [{"phrase": "rope change urgent", "count": 3, "hours": 3.0}]


def test_analyze_overtime_category_detail_drilldown():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))
    by_cat = {c["category"]: c for c in result["category_detail"]}

    daily = by_cat["Daily Checks"]
    assert daily["instances"] == 7
    assert daily["hours"] == 14.0
    assert daily["avg_hours"] == 2.0
    assert daily["pct_of_total"] == round(14.0 / 29.0 * 100)
    assert daily["top_weekday"] == "Mon"
    assert daily["top_employee"] == "Alice Smith"
    assert daily["top_spare"] == "Grease Cartridge"
    assert len(daily["records"]) == 7

    loco = by_cat["Loco Breakdown"]
    assert loco["top_weekday"] == "Wed"
    assert loco["top_employee"] == "Bob Jones"
    assert loco["top_spare"] == "Brake Pad"


def test_analyze_overtime_machine_cross_reference(monkeypatch):
    # "winder" appears (case-insensitively) in both Alice's and Bob's reason text —
    # cross-referenced against the equipment register, it must be reported as a
    # CONFIRMED machine mention with the combined hours/count from both employees.
    monkeypatch.setattr(overtime_mod, "_equipment_names", lambda: ["winder", "loco 5"])

    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))

    assert result["top_machines"] == [{"name": "Winder", "count": 11, "hours": 26.0}]


def test_analyze_overtime_machine_matching_skips_records_with_no_reason_text(monkeypatch):
    monkeypatch.setattr(overtime_mod, "_equipment_names", lambda: ["winder"])
    records = _build_fixture() + [_rec("Dan", "Processing", "2024-03-01", "", hours=1.0)]

    result = _analyze_overtime(OvertimeAnalysisInput(records=records))

    # The blank-reason record must not blow up or get counted as a machine mention.
    assert result["top_machines"] == [{"name": "Winder", "count": 11, "hours": 26.0}]


def test_analyze_overtime_no_equipment_match_is_empty_top_machines():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))
    assert result["top_machines"] == []


def test_analyze_overtime_weekly_series_sums_hours_per_iso_week():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))
    weekly = {w["week"]: w["hours"] for w in result["weekly_series"]}
    # ISO week 2024-W01 (Jan 1-7) holds Alice's 2024-01-01 (2h), Bob's 2024-01-03 (3h),
    # and Carol's 2024-01-05 (1.5h) — 6.5h combined.
    assert weekly["2024-W01"] == 6.5
    # Every week's hours must sum back to the same total as total_hours.
    assert round(sum(weekly.values()), 1) == 29.0


def test_analyze_overtime_trend_direction_and_older_newer_split():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))
    trend = result["trends"][0]
    assert trend["metric"] == "Overtime Volume"
    assert trend["older_hours"] + trend["newer_hours"] == 29.0
    assert result["trend_direction"] in {"stable", "worsening", "improving"}
    assert trend["direction"] == result["trend_direction"]


def test_analyze_overtime_hour_weekday_matrix_places_hours_correctly():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))
    matrix = result["hour_weekday_hours"]
    # Alice starts at 17:00 on Mondays (weekday 0) — 7 records * 2h = 14h in that cell.
    assert matrix[17][0] == 14.0
    # Bob starts at 20:00 on Wednesdays (weekday 2) — 4 records * 3h = 12h.
    assert matrix[20][2] == 12.0
    # Carol's records carry `hours` only (no start_time) — they can't be placed on the
    # hour axis at all, so every other cell stays 0.
    assert sum(sum(row) for row in matrix) == 26.0


def test_analyze_overtime_punch_records_lists_underlying_entries():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))
    cell = result["punch_records"]["17-0"]
    assert len(cell) == 7
    assert all(e["employee_name"] == "Alice Smith" for e in cell)


def test_analyze_overtime_punch_records_capped_at_12_sorted_by_hours_desc():
    # 14 records in the same hour/weekday cell with distinct hours — only the top 12
    # (by hours, descending) must survive the cap.
    records = [
        _rec(f"Emp{i}", "Ops", "2024-01-01", "", start_time="09:00", hours=float(i))
        for i in range(1, 15)
    ]
    result = _analyze_overtime(OvertimeAnalysisInput(records=records))
    cell = result["punch_records"]["9-0"]
    assert len(cell) == 12
    assert [e["hours"] for e in cell] == sorted([e["hours"] for e in cell], reverse=True)
    assert cell[0]["hours"] == 14.0
    assert cell[-1]["hours"] == 3.0  # hours 14..3 survive; 1 and 2 are dropped


def test_analyze_overtime_problem_areas_and_recommendations_present():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))
    titles = {p["title"] for p in result["possible_causes"]}

    assert "Overtime Concentrated on Alice Smith" in titles  # 14/29 = 48% >= 25%, >=4 records
    assert 'Recurring Cause: "Daily Checks"' in titles  # count 7 >= 3
    assert 'Recurring Cause: "Loco Breakdown"' in titles  # count 4 >= 3
    assert "Processing Carries a Disproportionate Overtime Load" in titles  # 26/29=90% >=40%, >1 section
    assert "High Weekend/Holiday (2.0x) Overtime Share" in titles  # 9/29=31% >= 30%

    actions = {r["action"] for r in result["recommendations"]}
    assert "Redistribute overtime load away from Alice Smith" in actions
    assert 'Investigate equipment reliability behind "Daily Checks"' in actions  # top_spare + instances>=3
    assert 'Investigate equipment reliability behind "Loco Breakdown"' in actions
    assert "Establish a monthly overtime review with section heads" in actions  # always present


def test_analyze_overtime_problem_areas_sorted_by_severity():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    severities = [sev_order[p["severity"]] for p in result["possible_causes"]]
    assert severities == sorted(severities)


def test_analyze_overtime_summary_mentions_top_concern_and_recommendation_count():
    result = _analyze_overtime(OvertimeAnalysisInput(records=_build_fixture()))
    assert "13 overtime record(s) analysed, 29.0h total" in result["summary"]
    assert "alice smith" in result["summary"].lower()
    assert f"{len(result['recommendations'])} recommendation(s) generated" in result["summary"]


# ─── Empty / no-concentration edge cases ───────────────────────────────────────────

def test_analyze_overtime_empty_records():
    result = _analyze_overtime(OvertimeAnalysisInput(records=[]))

    assert result["total_instances"] == 0
    assert result["total_hours"] == 0
    assert result["employees_involved"] == 0
    assert result["avg_hours_per_instance"] == 0
    assert result["avg_hours_per_employee"] == 0
    assert result["double_time_pct"] == 0
    assert result["top_employees"] == []
    assert result["possible_causes"] == []
    assert result["trends"] == []  # `if total > 0` guards trend entry
    assert "No overtime records in the current view" in result["summary"]
    # The base long-term recommendation is unconditional — always present even at zero.
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["priority"] == "long_term"


def test_analyze_overtime_no_concentration_yields_no_problem_areas():
    # Six employees, each an equal ~17% share (below the 25% concentration threshold),
    # single department (dept-load check needs >1 section to compare against), no
    # double-time types, six reasons sharing no bigram/trigram with each other, and an
    # EVEN record count so the older/newer trend split (3 vs 3) stays perfectly flat.
    records = [
        _rec("A", "Ops", "2024-01-01", "electrical panel repair", hours=2.0),
        _rec("B", "Ops", "2024-01-02", "water pump replacement", hours=2.0),
        _rec("C", "Ops", "2024-01-03", "conveyor belt alignment", hours=2.0),
        _rec("D", "Ops", "2024-01-04", "hydraulic hose leak", hours=2.0),
        _rec("E", "Ops", "2024-01-05", "generator fuel top-up", hours=2.0),
        _rec("F", "Ops", "2024-01-06", "ventilation fan inspection", hours=2.0),
    ]
    result = _analyze_overtime(OvertimeAnalysisInput(records=records))

    assert result["possible_causes"] == []
    # Only the unconditional monthly-review recommendation remains — every other
    # recommendation is gated behind a threshold none of these records clear.
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["action"] == "Establish a monthly overtime review with section heads"


def test_analyze_overtime_department_none_buckets_as_unassigned_and_is_excluded_from_count():
    records = [
        _rec("A", None, "2024-01-01", "task one", hours=2.0),
        _rec("B", None, "2024-01-02", "task two", hours=2.0),
    ]
    result = _analyze_overtime(OvertimeAnalysisInput(records=records))
    assert result["sections_involved"] == 0
    assert result["top_sections"] == [{"section": "Unassigned", "hours": 4.0}]
    # 'Unassigned' must never trigger the disproportionate-section problem area.
    assert not any("Disproportionate" in p["title"] for p in result["possible_causes"])


def test_analyze_overtime_worsening_trend_adds_staffing_recommendation():
    # First half light, second half much heavier (>20% increase) -> 'worsening'.
    records = [_rec("A", "Ops", "2024-01-01", "steady task one", hours=1.0)]
    records += [_rec("A", "Ops", "2024-03-01", "steady task two", hours=10.0)]
    result = _analyze_overtime(OvertimeAnalysisInput(records=records))

    assert result["trend_direction"] == "worsening"
    actions = [r["action"] for r in result["recommendations"]]
    assert "Review current staffing levels — overtime volume is trending up" in actions


def test_analyze_overtime_hours_field_used_when_no_start_end_times():
    # Confirms the orchestrator delegates duration calculation to _hours() rather than
    # re-deriving it — a record with only `hours` set must still be counted correctly.
    records = [_rec("A", "Ops", "2024-01-01", "x", hours=5.5)]
    result = _analyze_overtime(OvertimeAnalysisInput(records=records))
    assert result["total_hours"] == 5.5


# ─── _group_hours' polars branch (records > 20), exercised via the orchestrator ───────

def test_analyze_overtime_totals_correct_with_more_than_20_records():
    # _group_hours takes the polars path once len(records) > 20 — this is the direct
    # integration check that the polars aggregation and the pure-python fallback agree.
    records = [
        _rec(f"Emp{i % 4}", f"Dept{i % 2}", "2024-01-01", f"unique reason {i}", hours=1.0)
        for i in range(25)
    ]
    result = _analyze_overtime(OvertimeAnalysisInput(records=records))
    assert result["total_instances"] == 25
    assert result["total_hours"] == 25.0
    assert result["employees_involved"] == 4
    assert sum(e["hours"] for e in result["top_employees"]) <= 25.0  # top 5 capped, all 4 fit


def test_group_hours_falls_back_to_pure_python_when_polars_aggregation_fails(monkeypatch):
    # Force the polars .DataFrame/.group_by call to raise — _group_hours must swallow
    # it and fall through to the pure-Python loop rather than propagating, matching the
    # existing "best-effort, degrade gracefully" pattern used elsewhere in this file.
    class _BrokenPolars:
        def DataFrame(self, rows):
            raise RuntimeError("polars broke")

    monkeypatch.setattr(overtime_mod, "pl", _BrokenPolars())
    records = [{"department": "Ops", "hours": 1.0} for _ in range(25)]

    result = _group_hours(records, lambda r: r.get("department"))

    assert result == {"Ops": 25.0}


# ─── _equipment_names — best-effort cross-reference source, isolated from the above ───

class _EquipResp:
    def __init__(self, data):
        self.data = data


def test_equipment_names_lowercases_and_filters_short_names(monkeypatch):
    class _Q:
        def select(self, *a, **k): return self
        def limit(self, n): return self
        def execute(self): return _EquipResp([{"name": "Winder A"}, {"name": "Jib"}, {"name": " Loco 5 "}])

    class _S:
        def table(self, name):
            assert name == "equipment"
            return _Q()

    monkeypatch.setattr(overtime_mod, "supabase", _S())
    names = _equipment_names()
    # "Jib" (len 3, not > 3) is excluded; the rest are lowercased and trimmed.
    assert names == sorted(["winder a", "loco 5"])


def test_equipment_names_swallows_errors_and_returns_empty_list(monkeypatch):
    class _S:
        def table(self, name):
            raise Exception("db unreachable")

    monkeypatch.setattr(overtime_mod, "supabase", _S())
    assert _equipment_names() == []


# ─── analyze_overtime — the route wrapper ─────────────────────────────────────────────

async def test_analyze_overtime_route_returns_the_same_shape():
    data = OvertimeAnalysisInput(records=_build_fixture())
    result = await analyze_overtime(data)
    assert result["total_instances"] == 13
    assert "recommendations" in result and "possible_causes" in result


async def test_analyze_overtime_route_wraps_failure_as_500(monkeypatch):
    def _boom(data):
        raise ValueError("unexpected shape")
    monkeypatch.setattr(overtime_mod, "_analyze_overtime", _boom)

    with pytest.raises(HTTPException) as exc:
        await analyze_overtime(OvertimeAnalysisInput(records=[]))
    assert exc.value.status_code == 500
    assert "unexpected shape" in exc.value.detail
