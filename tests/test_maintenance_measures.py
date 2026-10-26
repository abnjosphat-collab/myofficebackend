# tests/test_maintenance_measures.py — M5: every measure has a hand-checked
# answer. Fixtures are tiny on purpose: each expected number below was worked
# out by hand from the rows above it.
from datetime import date

import pytest

import app.routers.maintenance as m
from app.routers.maintenance import get_maintenance_measures
from app.maintenance_measures import (
    backlog_by_age,
    compute_measures,
    cost_by_machine,
    machine_downtime,
    opened_vs_completed,
    parse_hours,
    pm_on_time,
    repeat_failures,
    worst_machines,
)

TODAY = date(2026, 10, 26)  # a Monday
START = date(2026, 7, 28)  # TODAY - 90 days


def _wo(i, **over):
    row = {"id": i, "status": "pending", "classification": None, "equipment_info": "Pump A",
           "date_raised": "2026-10-20", "due_date": None, "completed_at": None,
           "artisan_date": "", "failure_mode": "", "total_time_worked": "", "spares_used": []}
    row.update(over)
    return row


def _bd(i, **over):
    row = {"id": i, "machine_name": "Crusher 1", "breakdown_date": "2026-10-01",
           "breakdown_start": "08:00", "downtime_minutes": None, "repair_time_minutes": None}
    row.update(over)
    return row


# ─── parse_hours ──────────────────────────────────────────────────────────────

def test_parse_hours_reads_the_shapes_close_writes():
    assert parse_hours("7h 30m") == 7.5
    assert parse_hours("2h 00m") == 2.0
    assert parse_hours("3.5") == 3.5
    assert parse_hours("") == 0.0
    assert parse_hours(None) == 0.0


# ─── 1. PM done on time ───────────────────────────────────────────────────────

def test_pm_on_time_counts_due_planned_jobs_and_the_on_time_share():
    orders = [
        _wo(1, classification="planned_maintenance", status="completed", due_date="2026-10-01",
            completed_at="2026-09-30T10:00:00"),  # on time (stamp beats the due date)
        _wo(2, classification="planned_maintenance", status="completed", due_date="2026-10-01",
            artisan_date="2026-10-05"),  # late (no stamp: the artisan's date counts)
        _wo(3, classification="planned_maintenance", status="pending", due_date="2026-10-10"),  # missed
        _wo(4, classification="planned_maintenance", status="cancelled", due_date="2026-10-10"),  # out: withdrawn
        _wo(5, classification="planned_maintenance", status="completed", due_date="2026-01-01",
            completed_at="2026-01-01T10:00:00"),  # out: due before the period
        _wo(6, classification="breakdown", status="completed", due_date="2026-10-01",
            completed_at="2026-09-30T10:00:00"),  # out: not a planned job
        _wo(7, classification="planned_maintenance", status="pending", due_date=None),  # out: no due date
        _wo(8, classification="planned_maintenance", status="completed", due_date="2026-10-01"),  # in, not on time: no date to prove it
    ]
    got = pm_on_time(orders, START, TODAY)
    assert got["total"] == 4
    assert got["on_time"] == 1
    assert got["percent"] == 25
    assert got["ids"] == [1, 2, 3, 8]
    assert got["on_time_ids"] == [1]


def test_pm_on_time_with_no_due_jobs_is_no_data_not_zero():
    assert pm_on_time([_wo(1)], START, TODAY)["percent"] is None


# ─── 2. backlog by age ────────────────────────────────────────────────────────

def test_backlog_by_age_buckets_open_jobs_by_date_raised():
    orders = [
        _wo(1, status="pending", date_raised="2026-10-20"),  # 6 days: 0-7
        _wo(2, status="in-progress", date_raised="2026-10-01"),  # 25 days: 8-30
        _wo(3, status="on-hold", date_raised="2026-08-01"),  # 86 days: 31-90
        _wo(4, status="pending", date_raised="2026-05-01"),  # over 90
        _wo(5, status="postponed", date_raised=""),  # unknown
        _wo(6, status="completed", date_raised="2026-10-20"),  # out: terminal
        _wo(7, status="not-done", date_raised="2026-05-01"),  # out: terminal
        _wo(8, status="cancelled", date_raised="2026-05-01"),  # out: terminal
    ]
    got = backlog_by_age(orders, TODAY)
    assert got["total"] == 5
    assert [(b["label"], b["count"], b["ids"]) for b in got["buckets"]] == [
        ("0-7 days", 1, [1]),
        ("8-30 days", 1, [2]),
        ("31-90 days", 1, [3]),
        ("Over 90 days", 1, [4]),
        ("Date unknown", 1, [5]),
    ]


# ─── 3. opened vs completed per week ──────────────────────────────────────────

def test_opened_vs_completed_places_jobs_in_monday_weeks():
    orders = [
        _wo(1, date_raised="2026-10-20"),  # week of the 19th: opened
        _wo(2, status="completed", date_raised="2026-10-26", completed_at="2026-10-26T09:00:00"),  # week of the 26th: opened + completed
        _wo(3, status="completed", date_raised="2026-10-10", artisan_date="2026-10-21"),  # opened earlier, completed in week one
        _wo(4, date_raised=""),  # undated open
        _wo(5, status="completed", date_raised="2026-10-22"),  # opened in week one, completed on no known date
    ]
    got = opened_vs_completed(orders, 2, TODAY)
    assert [(w["week_start"], w["opened"], w["completed"]) for w in got["weeks"]] == [
        ("2026-10-19", 2, 1),
        ("2026-10-26", 1, 1),
    ]
    assert got["weeks"][0]["opened_ids"] == [1, 5]
    assert got["weeks"][0]["completed_ids"] == [3]
    assert got["weeks"][1]["opened_ids"] == [2]
    assert got["weeks"][1]["completed_ids"] == [2]
    assert got["undated_opened"] == [4]
    assert got["undated_completed"] == [5]


# ─── 4. downtime / MTTR / MTBF per machine ────────────────────────────────────

def test_machine_downtime_groups_by_machine_name_case_insensitively():
    rows = [
        _bd(1, machine_name="Crusher 1", breakdown_date="2026-10-01", downtime_minutes=120, repair_time_minutes=90),
        _bd(2, machine_name="crusher 1", breakdown_date="2026-10-11", downtime_minutes=60, repair_time_minutes=30),
        _bd(3, machine_name="Pump A", breakdown_date="2026-10-05", downtime_minutes=45, repair_time_minutes=None),
        _bd(4, machine_name="Crusher 1", breakdown_date="2026-01-01", downtime_minutes=999, repair_time_minutes=999),  # out: before the period
        _bd(5, machine_name="Mill 3", breakdown_date=""),  # out: no date
    ]
    got = machine_downtime(rows, START, TODAY)["rows"]
    assert len(got) == 2
    crusher, pump = got[0], got[1]  # most breakdowns first
    assert crusher["breakdowns"] == 2
    assert crusher["downtime_hours"] == 3.0  # (120 + 60) / 60
    assert crusher["mttr_hours"] == 1.0  # mean(90, 30) / 60
    assert crusher["mtbf_days"] == 10.0  # 1 Oct 08:00 -> 11 Oct 08:00
    assert crusher["breakdown_ids"] == [1, 2]
    assert pump["breakdowns"] == 1
    assert pump["downtime_hours"] == 0.8  # 45 / 60, one decimal
    assert pump["mttr_hours"] is None  # no repair time recorded
    assert pump["mtbf_days"] is None  # a single breakdown has no gap


# ─── 5. repeat failures ───────────────────────────────────────────────────────

def test_repeat_failures_groups_machine_and_code_within_30_and_90_days():
    orders = [
        _wo(1, classification="breakdown", equipment_info="Crusher 1", failure_mode="Bearing failure", date_raised="2026-10-01"),
        _wo(2, classification="breakdown", equipment_info="crusher 1", failure_mode="bearing failure", date_raised="2026-10-20"),
        _wo(3, classification="breakdown", equipment_info="Pump A", failure_mode="Seal leak", date_raised="2026-10-01"),
        _wo(4, classification="breakdown", equipment_info="Pump A", failure_mode="Seal leak", date_raised="2026-08-25"),
        _wo(5, classification="breakdown", equipment_info="Pump A", failure_mode="Seal leak", date_raised="2026-05-01"),  # out: older than 90 days
        _wo(6, classification="planned_maintenance", equipment_info="Crusher 1", failure_mode="Bearing failure", date_raised="2026-10-02"),  # out: not a breakdown
        _wo(7, classification="breakdown", equipment_info="Crusher 1", failure_mode="", date_raised="2026-10-03"),  # out: no code
        _wo(8, classification="breakdown", equipment_info="Mill 3", failure_mode="Crack", date_raised="2026-10-03"),  # out: seen once
    ]
    got = repeat_failures(orders, TODAY)
    assert got["groups_30d"] == 1
    assert got["groups_90d"] == 1
    assert [(g["count"], g["window_days"], g["ids"]) for g in got["groups"]] == [
        (2, 30, [1, 2]),  # 19-day span
        (2, 90, [4, 3]),  # 37-day span, oldest first
    ]


# ─── 6. worst machines ────────────────────────────────────────────────────────

def test_worst_machines_ranks_by_breakdowns_with_open_jobs_name_matched():
    breakdowns = [
        _bd(1, machine_name="Crusher 1", breakdown_date="2026-10-01", downtime_minutes=120),
        _bd(2, machine_name="Crusher 1", breakdown_date="2026-10-11", downtime_minutes=60),
        _bd(3, machine_name="Pump A", breakdown_date="2026-10-05", downtime_minutes=45),
    ]
    orders = [
        _wo(11, status="pending", equipment_info="Crusher 1"),
        _wo(12, status="in-progress", equipment_info="CRUSHER 1 "),  # spacing and case do not matter
        _wo(13, status="completed", equipment_info="Crusher 1"),  # out: closed
        _wo(14, status="pending", equipment_info="Other rig"),  # no breakdowns: never listed
    ]
    got = worst_machines(breakdowns, orders, START, TODAY)["rows"]
    assert [(r["machine"], r["breakdowns"], r["open_jobs"]) for r in got] == [
        ("Crusher 1", 2, 2),
        ("Pump A", 1, 0),
    ]
    assert got[0]["downtime_hours"] == 3.0
    assert got[0]["breakdown_ids"] == [1, 2]
    assert got[0]["work_order_ids"] == [11, 12]


# ─── 7. labour hours and spares cost per machine ──────────────────────────────

def test_cost_by_machine_sums_recorded_time_and_spares():
    orders = [
        _wo(1, equipment_id=7, equipment_info="Crusher 1", date_raised="2026-10-01",
            total_time_worked="2h 30m", spares_used=[{"quantity": 2, "unit_cost": 10}]),
        _wo(2, equipment_info="Pump A", date_raised="2026-10-02",
            total_time_worked="3", spares_used='[{"quantity": 1, "unit_cost": 5.5}]'),  # stored as a JSON string
        _wo(3, equipment_info="Pump A", date_raised="2026-01-01",
            total_time_worked="9h 00m", spares_used=[{"quantity": 9, "unit_cost": 9}]),  # out: raised before the period
        _wo(4, equipment_info="", date_raised="2026-10-03"),  # no time, no spares, no machine
    ]
    got = cost_by_machine(orders, START, TODAY, {7: "Crusher 1"})["rows"]
    assert [(r["machine"], r["labour_hours"], r["spares_cost"], r["jobs"]) for r in got] == [
        ("Pump A", 3.0, 5.5, 1),
        ("Crusher 1", 2.5, 20.0, 1),
        ("Unknown machine", 0.0, 0.0, 1),
    ]
    assert got[1]["equipment_id"] == 7
    assert got[0]["equipment_id"] is None
    assert got[0]["ids"] == [2]


# ─── the endpoint ─────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state):
        self.table_name = table_name
        self.state = state

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        return _Resp(list(self.state.get(self.table_name, [])))


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(name, self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {}

    def _patch(work_orders=None, breakdowns=None, equipment=None):
        state["work_orders"] = list(work_orders or [])
        state["breakdowns"] = list(breakdowns or [])
        state["equipment"] = list(equipment or [])
        monkeypatch.setattr(m, "supabase", _FakeSupabase(state))
        return state

    return _patch


async def test_measures_endpoint_answers_all_seven_over_scoped_rows(patch_supabase):
    patch_supabase(
        work_orders=[
            _wo(1, classification="planned_maintenance", status="completed", due_date="2026-10-01",
                date_raised="2026-09-20", completed_at="2026-09-30T10:00:00",
                total_time_worked="1h 00m"),
            _wo(2, status="pending", date_raised="2026-10-20"),
        ],
        breakdowns=[_bd(9, breakdown_date="2026-10-05", downtime_minutes=60, repair_time_minutes=30)],
        equipment=[{"id": 7, "name": "Crusher 1"}],
    )
    got = await get_maintenance_measures(days=90, weeks=4)
    assert got["period"]["days"] == 90
    assert got["period"]["weeks"] == 4
    assert got["pm_on_time"]["total"] == 1
    assert got["pm_on_time"]["percent"] == 100
    assert got["backlog_by_age"]["total"] == 1
    assert len(got["opened_vs_completed"]["weeks"]) == 4
    assert got["machine_downtime"]["rows"][0]["machine"] == "Crusher 1"
    assert got["repeat_failures"]["groups"] == []
    assert got["worst_machines"]["rows"][0]["open_jobs"] == 0
    assert got["cost_by_machine"]["rows"][0]["labour_hours"] == 1.0


async def test_measures_endpoint_clamps_its_window(patch_supabase):
    patch_supabase()
    got = await get_maintenance_measures(days=9999, weeks=1)
    assert got["period"]["days"] == 365
    assert got["period"]["weeks"] == 4
    assert got["pm_on_time"]["percent"] is None  # empty tables: no data, not zeros


def test_compute_measures_labels_repeats_from_the_register():
    orders = [
        _wo(1, classification="breakdown", equipment_id=7, equipment_info="old name",
            failure_mode="Snap", date_raised="2026-10-01"),
        _wo(2, classification="breakdown", equipment_id=7, equipment_info="old name",
            failure_mode="Snap", date_raised="2026-10-02"),
    ]
    got = compute_measures(orders, [], {7: "Crusher 1"}, TODAY)
    assert got["repeat_failures"]["groups"][0]["machine"] == "Crusher 1"
