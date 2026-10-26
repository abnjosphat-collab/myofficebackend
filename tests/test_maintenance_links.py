# tests/test_maintenance_links.py — M1: register links and server-stamped times.
#
# Covers app/maintenance_links.py (marker parsing, exact-match resolution),
# the work-order lifecycle stamps (started/completed/signed-off, written once,
# never from the browser), schedule inheritance onto generated jobs, and the
# before/after-SQL tolerance (new keys are dropped until the M1 columns exist).
from datetime import date

import pytest

import app.routers.compressors as comp_mod
import app.routers.maintenance as m
import app.routers.schedules as sched_mod
from app.maintenance_links import (
    PLANNED_MAINTENANCE,
    ROUTINE_MARKER_PREFIX,
    parse_routine_marker,
    resolve_equipment_id,
)
from app.routers.compressors import (
    ROUTINE_MARKER_PREFIX as COMPRESSOR_PREFIX,
    ensure_weekly_routine_schedules,
)
from app.routers.maintenance import (
    WorkOrderCreate,
    WorkOrderUpdate,
    _lifecycle_stamps,
    create_work_order,
    get_work_orders,
    update_work_order,
)
from app.routers.schedules import ScheduleCreate, _generate_one, create_schedule


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state, config):
        self.table_name = table_name
        self.state = state
        self.config = config.get(table_name, {})
        self._filters = []
        self._op = "select"
        self._payload = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def ilike(self, col, pattern):
        self._filters.append((col, pattern))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        cfg = self.config
        raises_key = f"{self._op}_raises"
        if raises_key in cfg:
            raise cfg[raises_key]
        key = f"{self._op}_return"
        if key in cfg:
            val = cfg[key]
            return _Resp(val(self._payload, self._filters) if callable(val) else val)
        if self._op == "insert":
            return _Resp([{"id": 1, **(self._payload or {})}])
        if self._op == "update":
            rows = cfg.get("rows", [])
            matched = [r for r in rows if all(r.get(c) == v for c, v in self._filters)]
            for r in matched:
                r.update(self._payload or {})
            return _Resp(matched)
        if self._op == "select":
            # select_return: canned rows, filters ignored (the generation-test
            # convention). rows: a tiny table the eq filters actually apply to.
            if "select_return" in cfg:
                val = cfg["select_return"]
                return _Resp(val(self._payload, self._filters) if callable(val) else val)
            rows = cfg.get("rows", [])
            matched = [r for r in rows if all(r.get(c) == v for c, v in self._filters)]
            return _Resp(matched)
        return _Resp([])


class _FakeSupabase:
    def __init__(self, state, config):
        self.state = state
        self.config = config

    def table(self, name):
        return _FakeQuery(name, self.state, self.config)


class _BrokenRedis:
    async def get(self, *a, **k):
        raise ConnectionError("no redis in tests")

    async def set(self, *a, **k):
        raise ConnectionError("no redis in tests")

    async def sadd(self, *a, **k):
        raise ConnectionError("no redis in tests")

    async def smembers(self, *a, **k):
        raise ConnectionError("no redis in tests")

    async def delete(self, *a, **k):
        raise ConnectionError("no redis in tests")


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    from app import cache as cache_mod

    monkeypatch.setattr(cache_mod, "redis_client", _BrokenRedis())


@pytest.fixture
def columns(monkeypatch):
    """Pretend the M1 SQL has (True) or has not (False) been run, on every router."""

    def _set(present: bool):
        monkeypatch.setattr(m, "has_columns", lambda *a, **k: present)
        monkeypatch.setattr(sched_mod, "has_columns", lambda *a, **k: present)
        monkeypatch.setattr(comp_mod, "has_columns", lambda *a, **k: present)

    return _set


@pytest.fixture
def patch_maintenance(monkeypatch):
    def _patch(config: dict):
        state = {"calls": []}
        monkeypatch.setattr(m, "supabase", _FakeSupabase(state, config))
        return state

    return _patch


@pytest.fixture
def patch_schedules(monkeypatch):
    def _patch(config: dict):
        state = {"calls": []}
        monkeypatch.setattr(sched_mod, "supabase", _FakeSupabase(state, config))
        return state

    return _patch


def _calls(state, table, op=None):
    return [c for c in state["calls"] if c["table"] == table and (op is None or c["op"] == op)]


def _user():
    return {"user_id": "u-1", "email": "u@x.com", "role": "user", "name": "Test User"}


REGISTER = [
    {"id": 26, "name": "Compressor No 1 "},  # trailing space, as live
    {"id": 40, "name": "Lobe Shaft Winder"},
    {"id": 56, "name": "10L Venning Winch 2"},
]


# ─── Marker parsing ──────────────────────────────────────────────────────────


def test_marker_prefix_matches_the_compressor_owner():
    assert ROUTINE_MARKER_PREFIX == COMPRESSOR_PREFIX == "compressor-routine:"


def test_parse_routine_marker_accepts_only_exact_markers():
    assert parse_routine_marker("compressor-routine:26") == 26
    assert parse_routine_marker("  compressor-routine:7  ") == 7
    assert parse_routine_marker("compressor-routine:") is None
    assert parse_routine_marker("compressor-routine:abc") is None
    assert parse_routine_marker("xcompressor-routine:26") is None
    assert parse_routine_marker("Compressor No 1") is None
    assert parse_routine_marker("") is None
    assert parse_routine_marker(None) is None
    assert parse_routine_marker(26) is None


# ─── Register resolution ─────────────────────────────────────────────────────


def test_resolve_equipment_id_exact_match():
    client = _FakeSupabase({"calls": []}, {"equipment": {"select_return": REGISTER}})
    assert resolve_equipment_id(client, "Lobe Shaft Winder") == 40


def test_resolve_equipment_id_trims_both_sides():
    # The typed name never carries the register's stray trailing space.
    client = _FakeSupabase({"calls": []}, {"equipment": {"select_return": REGISTER}})
    assert resolve_equipment_id(client, "Compressor No 1") == 26
    assert resolve_equipment_id(client, "  Lobe Shaft Winder  ") == 40


def test_resolve_equipment_id_marker_needs_a_live_register_row():
    client = _FakeSupabase({"calls": []}, {"equipment": {"select_return": REGISTER}})
    assert resolve_equipment_id(client, "compressor-routine:26") == 26
    assert resolve_equipment_id(client, "compressor-routine:999") is None


def test_resolve_equipment_id_never_guesses():
    client = _FakeSupabase({"calls": []}, {"equipment": {"select_return": REGISTER}})
    assert resolve_equipment_id(client, "hbub") is None
    assert resolve_equipment_id(client, "lobe shaft winder") is None  # case differs
    assert resolve_equipment_id(client, "Lobe Shaft") is None  # partial
    assert resolve_equipment_id(client, "") is None
    assert resolve_equipment_id(client, None) is None
    assert resolve_equipment_id(client, "   ") is None


# ─── Lifecycle stamps ────────────────────────────────────────────────────────

NOW = "2026-10-26T08:00:00"


def test_stamps_start_on_first_move_to_in_progress(columns):
    columns(True)
    assert _lifecycle_stamps({"status": "pending"}, {"status": "in-progress"}, NOW) == {"started_at": NOW}


def test_stamps_direct_close_fills_both_start_and_completion(columns):
    columns(True)
    assert _lifecycle_stamps({"status": "pending"}, {"status": "completed"}, NOW) == {
        "completed_at": NOW,
        "started_at": NOW,
    }


def test_stamps_completion_keeps_an_earlier_start(columns):
    columns(True)
    before = {"status": "in-progress", "started_at": "2026-10-25T07:00:00"}
    assert _lifecycle_stamps(before, {"status": "completed"}, NOW) == {"completed_at": NOW}


def test_stamps_never_overwrite_or_clear(columns):
    columns(True)
    before = {
        "status": "completed",
        "started_at": "2026-10-24T07:00:00",
        "completed_at": "2026-10-25T07:00:00",
        "foreman_sign": "M.C",
        "signed_off_at": "2026-10-25T08:00:00",
    }
    # Reopening keeps the history; re-completing keeps the first completion.
    assert _lifecycle_stamps(before, {"status": "in-progress"}, NOW) == {}
    assert _lifecycle_stamps(before, {"status": "completed"}, NOW) == {}
    assert _lifecycle_stamps(before, {"foreman_sign": "Someone Else"}, NOW) == {}


def test_stamps_sign_off_when_the_foreman_sign_first_appears(columns):
    columns(True)
    assert _lifecycle_stamps({"status": "completed", "foreman_sign": ""}, {"foreman_sign": "M.C"}, NOW) == {
        "signed_off_at": NOW
    }
    # A blank or whitespace-only sign is not a signature.
    assert _lifecycle_stamps({"status": "completed"}, {"foreman_sign": "   "}, NOW) == {}
    assert _lifecycle_stamps({"status": "completed", "foreman_sign": ""}, {"priority": "high"}, NOW) == {}


def test_stamps_nothing_without_a_transition(columns):
    columns(True)
    assert _lifecycle_stamps({"status": "pending"}, {"priority": "high"}, NOW) == {}
    assert _lifecycle_stamps({"status": "in-progress"}, {"status": "in-progress"}, NOW) == {}


def test_stamps_empty_until_the_m1_sql_runs(columns):
    columns(False)
    assert _lifecycle_stamps({"status": "pending"}, {"status": "completed"}, NOW) == {}
    assert _lifecycle_stamps({"status": "pending"}, {"foreman_sign": "M.C"}, NOW) == {}


def test_browser_cannot_send_stamps():
    # Integrity rule 2: extra fields are ignored, so a client-sent timestamp
    # never reaches the database.
    update = WorkOrderUpdate(status="completed", started_at="2001-01-01", completed_at="x", signed_off_at="y")  # type: ignore[call-arg]
    assert update.model_dump(exclude_unset=True) == {"status": "completed"}


async def test_patch_writes_stamps_into_the_update(columns, patch_maintenance):
    columns(True)
    before = {"id": 9, "status": "pending", "work_order_number": "WO-00009", "foreman_sign": ""}
    state = patch_maintenance({"work_orders": {"rows": [dict(before)]}})
    result = await update_work_order(9, WorkOrderUpdate(status="completed"), _user())
    payload = _calls(state, "work_orders", "update")[0]["payload"]
    assert payload["completed_at"] and payload["started_at"]
    assert result["completed_at"] == payload["completed_at"]


async def test_patch_pre_sql_writes_no_stamps(columns, patch_maintenance):
    columns(False)
    before = {"id": 9, "status": "pending", "work_order_number": "WO-00009", "foreman_sign": ""}
    state = patch_maintenance({"work_orders": {"rows": [dict(before)]}})
    await update_work_order(9, WorkOrderUpdate(status="completed"), _user())
    payload = _calls(state, "work_orders", "update")[0]["payload"]
    assert "completed_at" not in payload and "started_at" not in payload


# ─── Work-order create: links ────────────────────────────────────────────────


def _create(**overrides):
    base = dict(
        to_department="Eng",
        to_section="S",
        date_raised="2026-10-26",
        work_order_number="WO-CLIENT",
        from_department="Min",
        from_section="O",
        time_raised="08:00",
        account_number="A",
        equipment_info="Lobe Shaft Winder",
        user_lab_today="Yes",
        job_type={"maintenance": True},
        job_request_details="Fix it",
        requested_by="J",
        authorising_foreman="F",
        authorising_engineer="E",
        allocated_to="A",
        estimated_hours="2",
        responsible_foreman="F",
        job_instructions="Do it",
        work_done_details="",
        cause_of_failure="",
        delay_details="",
        artisan_name="",
        artisan_sign="",
        artisan_date="",
        foreman_name="",
        foreman_sign="",
        foreman_date="",
        time_work_started="",
        time_work_finished="",
        total_time_worked="",
        overtime_start_time="",
        overtime_end_time="",
        overtime_hours="",
        delay_from_time="",
        delay_to_time="",
        total_delay_hours="",
    )
    base.update(overrides)
    return WorkOrderCreate(**base)


async def test_create_resolves_an_exact_machine_name(columns, patch_maintenance):
    columns(True)
    state = patch_maintenance(
        {
            "equipment": {"select_return": REGISTER},
            "work_orders": {"select_return": []},
        }
    )
    result = await create_work_order(_create(), _user())
    payload = _calls(state, "work_orders", "insert")[0]["payload"]
    assert payload["equipment_id"] == 40
    assert result["equipment_id"] == 40


async def test_create_keeps_a_picker_sent_link_and_leaves_unknown_names_blank(columns, patch_maintenance):
    columns(True)
    state = patch_maintenance(
        {
            "equipment": {"select_return": REGISTER},
            "work_orders": {"select_return": []},
        }
    )
    await create_work_order(_create(equipment_id=56, equipment_info="Whatever typed"), _user())
    assert _calls(state, "work_orders", "insert")[0]["payload"]["equipment_id"] == 56
    # No equipment read for the resolved row: the sent link is trusted as-is.
    assert [c for c in state["calls"] if c["table"] == "equipment"] == []

    await create_work_order(_create(equipment_info="hbub"), _user())
    assert _calls(state, "work_orders", "insert")[1]["payload"]["equipment_id"] is None


async def test_create_pre_sql_drops_the_link_keys(columns, patch_maintenance):
    columns(False)
    state = patch_maintenance({"work_orders": {"select_return": []}})
    await create_work_order(_create(equipment_id=40, schedule_id=3), _user())
    payload = _calls(state, "work_orders", "insert")[0]["payload"]
    assert "equipment_id" not in payload and "schedule_id" not in payload
    assert [c for c in state["calls"] if c["table"] == "equipment"] == []


# ─── List filters ────────────────────────────────────────────────────────────


async def test_list_filters_by_equipment_and_schedule(columns, patch_maintenance):
    columns(True)
    state = patch_maintenance({"work_orders": {"rows": []}})
    await get_work_orders(equipment_id=40, schedule_id=3, current_user=_user())
    filters = _calls(state, "work_orders", "select")[0]["filters"]
    assert ("equipment_id", 40) in filters
    assert ("schedule_id", 3) in filters


async def test_list_link_filters_are_empty_pre_sql(columns, patch_maintenance):
    columns(False)
    state = patch_maintenance({"work_orders": {"rows": [{"id": 1}]}})
    assert await get_work_orders(equipment_id=40, current_user=_user()) == []
    assert _calls(state, "work_orders") == []


# ─── Generation: inheritance ─────────────────────────────────────────────────


def _schedule(**overrides):
    base = {
        "id": 4,
        "name": "Lobe shaft weekly p.m",
        "equipment_info": "Lobe Shaft Winder",
        "to_department": "Engineering",
        "allocated_to": "J. Moyo",
        "authorising_foreman": "T. Ncube",
        "estimated_hours": "2",
        "job_request_details": "Weekly PM",
        "job_instructions": "See SOP",
        "priority": "medium",
        "recurrence_type": "weekly",
        "next_due_date": date(2026, 10, 30),
        "active": True,
        "discipline": "Mechanical",
        "trade": "Fitter",
    }
    base.update(overrides)
    return base


def test_generate_inherits_links_and_classification(columns, patch_schedules):
    columns(True)
    state = patch_schedules(
        {
            "maintenance_schedule_runs": {"insert_return": lambda p, f: [{"id": 10, **p}]},
            "work_orders": {
                "select_return": [{"work_order_number": "WO-00003"}],
                "insert_return": lambda p, f: [{"id": 55, **p}],
            },
        }
    )
    result = _generate_one(_schedule(equipment_id=40), date(2026, 10, 30))
    assert result["schedule_id"] == 4
    payload = _calls(state, "work_orders", "insert")[0]["payload"]
    assert payload["schedule_id"] == 4
    assert payload["equipment_id"] == 40
    assert payload["classification"] == PLANNED_MAINTENANCE == "planned_maintenance"
    assert payload["discipline"] == "Mechanical"
    assert payload["trade"] == "Fitter"
    assert [c for c in state["calls"] if c["table"] == "equipment"] == []


def test_generate_resolves_marker_and_name_when_the_schedule_has_no_link(columns, patch_schedules):
    columns(True)
    state = patch_schedules(
        {
            "maintenance_schedule_runs": {"insert_return": lambda p, f: [{"id": 10, **p}]},
            "work_orders": {
                "select_return": [],
                "insert_return": lambda p, f: [{"id": 55, **p}],
            },
            "equipment": {"select_return": REGISTER},
        }
    )
    _generate_one(_schedule(equipment_info="compressor-routine:26"), date(2026, 10, 26))
    assert _calls(state, "work_orders", "insert")[0]["payload"]["equipment_id"] == 26

    _generate_one(_schedule(equipment_info="Lobe Shaft Winder"), date(2026, 10, 26))
    assert _calls(state, "work_orders", "insert")[1]["payload"]["equipment_id"] == 40

    _generate_one(_schedule(equipment_info="hbub"), date(2026, 10, 26))
    assert _calls(state, "work_orders", "insert")[2]["payload"]["equipment_id"] is None


def test_generate_pre_sql_omits_links_but_keeps_classification(columns, patch_schedules):
    columns(False)
    state = patch_schedules(
        {
            "maintenance_schedule_runs": {"insert_return": lambda p, f: [{"id": 10, **p}]},
            "work_orders": {
                "select_return": [],
                "insert_return": lambda p, f: [{"id": 55, **p}],
            },
        }
    )
    result = _generate_one(_schedule(), date(2026, 10, 30))
    assert result is not None
    payload = _calls(state, "work_orders", "insert")[0]["payload"]
    assert "schedule_id" not in payload and "equipment_id" not in payload
    assert payload["classification"] == "planned_maintenance"
    assert [c for c in state["calls"] if c["table"] == "equipment"] == []


# ─── Schedule CRUD: M1 fields ────────────────────────────────────────────────


def test_schedule_create_defaults_to_planned_maintenance():
    s = ScheduleCreate(name="X", recurrence_type="weekly")
    assert s.classification == "planned_maintenance"
    assert s.equipment_id is None and s.discipline is None and s.trade is None


async def test_schedule_create_resolves_the_link(columns, patch_schedules, monkeypatch):
    columns(True)
    state = patch_schedules(
        {
            "equipment": {"select_return": REGISTER},
            "maintenance_schedules": {"insert_return": lambda p, f: [{"id": 8, **p}]},
        }
    )
    manager = {"user_id": "m-1", "email": "m@x.com", "role": "manager", "name": "M"}
    created = await create_schedule(
        ScheduleCreate(name="X", recurrence_type="weekly", equipment_info="Lobe Shaft Winder"), manager
    )
    assert created["equipment_id"] == 40
    assert _calls(state, "maintenance_schedules", "insert")[0]["payload"]["classification"] == "planned_maintenance"


async def test_schedule_create_pre_sql_drops_m1_keys(columns, patch_schedules):
    columns(False)
    state = patch_schedules(
        {
            "maintenance_schedules": {"insert_return": lambda p, f: [{"id": 8, **p}]},
        }
    )
    manager = {"user_id": "m-1", "email": "m@x.com", "role": "manager", "name": "M"}
    await create_schedule(
        ScheduleCreate(name="X", recurrence_type="weekly", equipment_id=40, discipline="Mechanical"), manager
    )
    payload = _calls(state, "maintenance_schedules", "insert")[0]["payload"]
    for key in ("equipment_id", "classification", "discipline", "trade"):
        assert key not in payload


# ─── Compressor routine ensure ───────────────────────────────────────────────


def test_routine_ensure_links_new_schedules_post_sql(columns, monkeypatch):
    columns(True)
    state = {"calls": []}
    fake = _FakeSupabase(
        state,
        {
            "equipment": {
                "select_return": [
                    {
                        "id": 7,
                        "equipment_id": "CMP 03",
                        "name": "CMP 03",
                        "model": "",
                        "location": "Compressor house",
                        "category": "Compressors",
                        "serial_number": "",
                    }
                ]
            },
            "maintenance_schedules": {"select_return": []},
            "compressors": {"select_return": []},
        },
    )
    result = ensure_weekly_routine_schedules(fake)
    assert result["checked"] == 1
    payload = _calls(state, "maintenance_schedules", "insert")[0]["payload"]
    assert payload["equipment_id"] == 7
    assert payload["classification"] == "planned_maintenance"
    assert payload["equipment_info"] == "compressor-routine:7"  # the marker stays


def test_resolve_respects_section_scope():
    scoped = _FakeSupabase(
        {"calls": []},
        {
            "equipment": {
                "rows": [
                    {"id": 40, "name": "Lobe Shaft Winder", "section": "Projects"},
                    {"id": 56, "name": "10L Venning Winch 2", "section": "Engineering"},
                ]
            }
        },
    )
    projects = {"user_id": "p-1", "role": "user", "scope": "Projects"}
    assert resolve_equipment_id(scoped, "Lobe Shaft Winder", projects) == 40
    assert resolve_equipment_id(scoped, "10L Venning Winch 2", projects) is None
    full = _FakeSupabase(
        {"calls": []},
        {"equipment": {"select_return": [{"id": 56, "name": "10L Venning Winch 2"}]}},
    )
    assert resolve_equipment_id(full, "10L Venning Winch 2", _user()) == 56
    assert resolve_equipment_id(full, "10L Venning Winch 2", None) == 56  # the cron has no viewer


def test_routine_ensure_omits_links_pre_sql(columns):
    columns(False)
    state = {"calls": []}
    fake = _FakeSupabase(
        state,
        {
            "equipment": {
                "select_return": [
                    {
                        "id": 7,
                        "equipment_id": "CMP 03",
                        "name": "CMP 03",
                        "model": "",
                        "location": "Compressor house",
                        "category": "Compressors",
                        "serial_number": "",
                    }
                ]
            },
            "maintenance_schedules": {"select_return": []},
            "compressors": {"select_return": []},
        },
    )
    ensure_weekly_routine_schedules(fake)
    payload = _calls(state, "maintenance_schedules", "insert")[0]["payload"]
    assert "equipment_id" not in payload and "classification" not in payload
