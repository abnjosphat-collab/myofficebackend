# tests/test_machine_history.py — M6: one machine's history has a hand-checked
# answer. The matching (register link, routine marker, normalized name) and
# every total below were worked out by hand from the rows above them.
from datetime import date

import pytest
from fastapi import HTTPException

import app.routers.equipment as eq_mod
from app.routers.equipment import get_equipment_history
from app.machine_history import (
    aggregate_spares,
    breakdown_matches,
    compute_machine_history,
    job_matches,
    schedule_matches,
)

TODAY = date(2026, 10, 26)  # a Monday
EQ = {"id": 7, "equipment_id": "CRS-1", "name": "Crusher 1"}


def _wo(i, **over):
    row = {"id": i, "status": "pending", "classification": None, "equipment_info": "Crusher 1",
           "equipment_id": None, "date_raised": "2026-10-20", "due_date": None,
           "completed_at": None, "artisan_date": "", "failure_mode": "",
           "total_time_worked": "", "spares_used": []}
    row.update(over)
    return row


def _bd(i, **over):
    row = {"id": i, "machine_name": "Crusher 1", "breakdown_date": "2026-10-01",
           "breakdown_start": "08:00", "downtime_minutes": None,
           "repair_time_minutes": None, "spares_used": []}
    row.update(over)
    return row


def _sc(i, **over):
    row = {"id": i, "name": "Weekly crush", "equipment_info": "Crusher 1",
           "equipment_id": None, "active": True, "recurrence_type": "weekly",
           "next_due_date": "2026-10-30"}
    row.update(over)
    return row


# ─── matching ────────────────────────────────────────────────────────────────

def test_job_matches_by_link_marker_and_normalized_name():
    assert job_matches(_wo(1, equipment_id=7, equipment_info="a renamed machine"), 7, "crusher 1", True)
    assert job_matches(_wo(2, equipment_info="compressor-routine:7"), 7, "crusher 1", False)  # marker needs no columns
    assert job_matches(_wo(3, equipment_info="  CRUSHER 1 "), 7, "crusher 1", False)  # case and spacing do not matter
    assert not job_matches(_wo(4, equipment_info="Pump A"), 7, "crusher 1", True)
    assert not job_matches(_wo(5, equipment_info="compressor-routine:8"), 7, "crusher 1", False)


def test_job_link_is_ignored_until_the_m1_columns_exist():
    linked_but_unlinked = _wo(1, equipment_id=7, equipment_info="another machine")
    assert not job_matches(linked_but_unlinked, 7, "crusher 1", False)
    assert job_matches(linked_but_unlinked, 7, "crusher 1", True)


def test_blank_register_name_matches_by_link_only():
    assert job_matches(_wo(1, equipment_id=7, equipment_info="anything"), 7, "", True)
    assert not job_matches(_wo(2, equipment_info=""), 7, "", True)
    assert not breakdown_matches(_bd("b", machine_name=""), "")


def test_schedule_matches_by_link_marker_and_name():
    assert schedule_matches(_sc(1, equipment_id=7, equipment_info="old text"), 7, "crusher 1", True)
    assert schedule_matches(_sc(2, equipment_info="compressor-routine:7"), 7, "crusher 1", False)
    assert schedule_matches(_sc(3, equipment_info="Crusher 1"), 7, "crusher 1", False)
    assert not schedule_matches(_sc(4, equipment_info="Pump A"), 7, "crusher 1", True)


def test_breakdown_matches_by_normalized_name_only():
    assert breakdown_matches(_bd("b1", machine_name="CRUSHER 1"), "crusher 1")
    assert not breakdown_matches(_bd("b2", machine_name="Pump A"), "crusher 1")


# ─── spares ──────────────────────────────────────────────────────────────────

def test_aggregate_spares_combines_job_and_breakdown_lines_by_name():
    orders = [_wo(1, spares_used=[
        {"name": "Bearing", "quantity": 2, "unit_cost": 10},
        {"name": "Grease", "quantity": 1, "unit_cost": 4},
        {"name": "", "quantity": 9, "unit_cost": 9},  # nameless: counted nowhere
    ])]
    breakdowns = [_bd("b1", spares_used=[
        {"name": "bearing", "quantity": 1, "unit_price": 12, "total_cost": 12},  # stored total wins
        {"name": "Bolt", "quantity": 4, "unit_price": 2.5},  # priced from quantity x unit
    ])]
    got = aggregate_spares(orders, breakdowns)
    assert [(r["name"], r["quantity"], r["cost"], r["jobs"], r["breakdowns"]) for r in got["rows"]] == [
        ("Bearing", 3.0, 32.0, 1, 1),
        ("Bolt", 4.0, 10.0, 0, 1),
        ("Grease", 1.0, 4.0, 1, 0),
    ]
    assert got["total_cost"] == 46.0


def test_aggregate_spares_reads_json_strings_and_skips_garbage():
    orders = [_wo(1, spares_used='[{"name": "Seal", "quantity": 1, "unit_cost": 5.5}]')]
    breakdowns = [_bd("b1", spares_used="not json"), _bd("b2", spares_used=[None, "x"])]
    got = aggregate_spares(orders, breakdowns)
    assert [(r["name"], r["cost"]) for r in got["rows"]] == [("Seal", 5.5)]
    assert got["total_cost"] == 5.5


# ─── the whole history, hand-checked ─────────────────────────────────────────

def _history_fixture():
    orders = [
        _wo(1, equipment_id=7, equipment_info="old name", status="completed",
            classification="planned_maintenance", date_raised="2026-09-20",
            due_date="2026-10-01", completed_at="2026-09-30T10:00:00",
            total_time_worked="2h 30m",
            spares_used=[{"name": "Bearing", "quantity": 2, "unit_cost": 10}]),
        _wo(2, equipment_info="crusher 1", date_raised="2026-10-20", total_time_worked="1h"),
        _wo(3, equipment_info="compressor-routine:7", date_raised="2026-10-21"),
        _wo(4, equipment_info="Pump A", date_raised="2026-10-22"),  # another machine
        _wo(5, equipment_id=7, equipment_info="Crusher 1", classification="breakdown",
            failure_mode="Snap", date_raised="2026-10-01"),
        _wo(6, equipment_info="CRUSHER 1", classification="breakdown",
            failure_mode="snap", date_raised="2026-10-10"),
        _wo(7, equipment_info="Crusher 1", status="completed", classification="planned_maintenance",
            date_raised="2026-09-25", due_date="2026-10-05", artisan_date="2026-10-06"),  # late
    ]
    breakdowns = [
        _bd("b1", breakdown_date="2026-10-01", downtime_minutes=120, repair_time_minutes=90,
            spares_used=[{"name": "Bearing", "quantity": 1, "unit_price": 12, "total_cost": 12}]),
        _bd("b2", machine_name="CRUSHER 1", breakdown_date="2026-10-11",
            downtime_minutes=60, repair_time_minutes=30),
        _bd("b3", machine_name="Pump A", breakdown_date="2026-10-05", downtime_minutes=45),  # elsewhere
        _bd("b4", breakdown_date="", downtime_minutes=30),  # undated: in the list, out of the measures
    ]
    schedules = [
        _sc(11, equipment_info="Crusher 1", active=True, next_due_date="2026-10-30"),
        _sc(12, equipment_id=7, equipment_info="whatever", active=False, next_due_date="2026-09-01"),
        _sc(13, equipment_info="Pump A", active=True, next_due_date="2026-10-27"),  # elsewhere
    ]
    return orders, breakdowns, schedules


def test_compute_history_lists_totals_and_measures():
    orders, breakdowns, schedules = _history_fixture()
    got = compute_machine_history(EQ, orders, breakdowns, schedules, TODAY)

    assert got["equipment"] == EQ
    assert got["period"] == {"days": 90, "start": "2026-07-28", "end": "2026-10-26"}
    assert got["links_active"] is True

    assert [o["id"] for o in got["jobs"]] == [3, 2, 6, 5, 7, 1]  # newest first
    assert [b["id"] for b in got["breakdowns"]] == ["b2", "b1", "b4"]
    assert [s["id"] for s in got["schedules"]] == [11, 12]  # active first
    assert got["spares"] == [{"name": "Bearing", "quantity": 3.0, "cost": 32.0, "jobs": 1, "breakdowns": 1}]

    assert got["totals"] == {
        "jobs": 6, "open_jobs": 4, "breakdowns": 3, "schedules": 2,
        "labour_hours": 3.5, "spares_cost": 32.0, "downtime_hours": 3.5,
        "counts_by_status": {"pending": 4, "completed": 2},
    }

    pm = got["measures"]["pm_on_time"]
    assert (pm["total"], pm["on_time"], pm["percent"], pm["ids"]) == (2, 1, 50, [7, 1])  # newest first, like the list

    dt = got["measures"]["downtime"]
    assert dt["breakdowns"] == 2 and dt["downtime_hours"] == 3.0
    assert dt["mttr_hours"] == 1.0 and dt["mtbf_days"] == 10.0
    assert sorted(dt["breakdown_ids"]) == ["b1", "b2"]

    repeats = got["measures"]["repeat_failures"]
    assert (repeats["groups_30d"], repeats["groups_90d"]) == (1, 0)
    assert repeats["groups"] == [{
        "machine": "Crusher 1", "code": "Snap", "count": 2, "window_days": 30,
        "first": "2026-10-01", "last": "2026-10-10", "ids": [5, 6],
    }]


def test_compute_history_with_no_records_is_empty_not_zero():
    got = compute_machine_history(EQ, [], [], [], TODAY)
    assert got["jobs"] == got["breakdowns"] == got["schedules"] == got["spares"] == []
    assert got["totals"]["jobs"] == 0 and got["totals"]["labour_hours"] == 0.0
    assert got["measures"]["pm_on_time"]["percent"] is None
    assert got["measures"]["downtime"] is None
    assert got["measures"]["repeat_failures"]["groups"] == []


def test_compute_history_pre_sql_matches_without_links():
    orders = [
        _wo(1, equipment_id=7, equipment_info="another machine"),  # link ignored
        _wo(2, equipment_info="Crusher 1"),
    ]
    got = compute_machine_history(EQ, orders, [], [], TODAY, links_active=False)
    assert [o["id"] for o in got["jobs"]] == [2]
    assert got["links_active"] is False


# ─── the endpoint ────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state, calls, failures):
        self.table_name = table_name
        self.state = state
        self.calls = calls
        self.failures = failures
        self.filters = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def execute(self):
        self.calls.append({"table": self.table_name, "filters": list(self.filters)})
        if self.table_name in self.failures:
            raise self.failures[self.table_name]
        rows = list(self.state.get(self.table_name, []))
        for col, val in self.filters:
            rows = [r for r in rows if r.get(col) == val]
        return _Resp(rows)


class _FakeSupabase:
    def __init__(self, state, calls, failures=None):
        self.state = state
        self.calls = calls
        self.failures = failures or {}

    def table(self, name):
        return _FakeQuery(name, self.state, self.calls, self.failures)


@pytest.fixture
def patch_supabase(monkeypatch):
    calls = []
    state = {}

    def _patch(equipment=None, work_orders=None, breakdowns=None, schedules=None,
               failures=None, links=True):
        state["equipment"] = list(equipment or [])
        state["work_orders"] = list(work_orders or [])
        state["breakdowns"] = list(breakdowns or [])
        state["maintenance_schedules"] = list(schedules or [])
        monkeypatch.setattr(eq_mod, "supabase", _FakeSupabase(state, calls, failures))
        monkeypatch.setattr(eq_mod, "has_columns", lambda client, table, cols: links)
        return calls

    return _patch


def _eq(**over):
    row = {"id": 7, "equipment_id": "CRS-1", "name": "Crusher 1", "section": None,
           "commission_date": "2022-03-01"}
    row.update(over)
    return row


async def test_history_endpoint_answers_the_computed_shape(patch_supabase):
    orders, breakdowns, schedules = _history_fixture()
    patch_supabase(equipment=[_eq()], work_orders=orders, breakdowns=breakdowns, schedules=schedules)
    got = await get_equipment_history(7, current_user={"user_id": "u1"})
    assert got["equipment"]["name"] == "Crusher 1"
    assert got["equipment"]["commission_date"] == date(2022, 3, 1)  # register dates converted
    assert got["totals"]["jobs"] == 6 and got["totals"]["breakdowns"] == 3
    assert got["measures"]["pm_on_time"]["percent"] == 50
    assert got["links_active"] is True


async def test_history_endpoint_clamps_its_window(patch_supabase):
    patch_supabase(equipment=[_eq()])
    assert (await get_equipment_history(7, days=9999, current_user={"user_id": "u1"}))["period"]["days"] == 365
    assert (await get_equipment_history(7, days=1, current_user={"user_id": "u1"}))["period"]["days"] == 7


async def test_history_endpoint_unknown_machine_is_404(patch_supabase):
    patch_supabase(equipment=[])
    with pytest.raises(HTTPException) as exc:
        await get_equipment_history(999, current_user={"user_id": "u1"})
    assert exc.value.status_code == 404


async def test_history_endpoint_hidden_machine_is_404(patch_supabase):
    patch_supabase(equipment=[_eq(section="Mining")])
    with pytest.raises(HTTPException) as exc:
        await get_equipment_history(7, current_user={"user_id": "u2", "scope": "Projects"})
    assert exc.value.status_code == 404


async def test_history_endpoint_scopes_the_lists(patch_supabase):
    calls = patch_supabase(
        equipment=[_eq(section="Projects")],
        work_orders=[_wo(1, section="Projects"), _wo(2, section="Mining")],
        breakdowns=[_bd("b1", section="Projects")],
        schedules=[_sc(11, section="Projects")],
    )
    got = await get_equipment_history(7, current_user={"user_id": "u2", "scope": "Projects"})
    assert [o["id"] for o in got["jobs"]] == [1]
    for table in ("work_orders", "breakdowns", "maintenance_schedules"):
        assert ("section", "Projects") in [tuple(f) for c in calls if c["table"] == table for f in c["filters"]]


async def test_history_endpoint_pre_sql_links_nothing(patch_supabase):
    patch_supabase(
        equipment=[_eq()],
        work_orders=[_wo(1, equipment_id=7, equipment_info="another machine")],
        links=False,
    )
    got = await get_equipment_history(7, current_user={"user_id": "u1"})
    assert got["links_active"] is False
    assert got["jobs"] == []


async def test_history_endpoint_db_failure_is_500(patch_supabase):
    patch_supabase(equipment=[_eq()], failures={"breakdowns": RuntimeError("boom")})
    with pytest.raises(HTTPException) as exc:
        await get_equipment_history(7, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500
