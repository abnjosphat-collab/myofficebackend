# tests/test_schedules_generation.py — the actual "cron job": _generate_one (turns
# one due schedule into a work order, atomically claimed via
# maintenance_schedule_runs' UNIQUE(schedule_id, due_date)) and generate_due_work_orders
# (the /generate endpoint that loads every active schedule and raises work orders for
# whichever ones are due). schedules.py's own module docstring explains why this can't
# live client-side: "a work order that only gets raised if somebody happens to open the
# page is not a schedule." Zero prior tests of this despite it being the real payload —
# test_schedules_recurrence.py only covers the pure date-math underneath it.
#
# _next_work_order_number (the "WO-00001" numbering used inside _generate_one) is also
# exercised here rather than in its own file — it's a small, single-purpose helper only
# ever called from _generate_one, not worth a third fake-supabase rig.

from datetime import date, datetime

import pytest
from fastapi import HTTPException

import app.routers.schedules as sched_mod
from app.routers.schedules import (
    _generate_one, _next_work_order_number, generate_due_work_orders,
)


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

    def select(self, *a, **k): return self
    def eq(self, col, val): self._filters.append((col, val)); return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def insert(self, data): self._op = "insert"; self._payload = data; return self
    def update(self, data): self._op = "update"; self._payload = data; return self
    def delete(self): self._op = "delete"; return self

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
            return _Resp([dict(self._payload)] if self._payload else [])
        return _Resp([])


class _FakeSupabase:
    def __init__(self, state, config):
        self.state = state
        self.config = config  # {table_name: {...}}

    def table(self, name):
        return _FakeQuery(name, self.state, self.config)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(config: dict):
        state = {"calls": []}
        monkeypatch.setattr(sched_mod, "supabase", _FakeSupabase(state, config))
        return state
    return _patch


def _calls(state, table, op=None):
    return [c for c in state["calls"] if c["table"] == table and (op is None or c["op"] == op)]


# ─── _next_work_order_number ─────────────────────────────────────────────────────

def test_next_work_order_number_uses_highest_trailing_digits(patch_supabase):
    patch_supabase({
        "work_orders": {"select_return": [{"work_order_number": "WO-00099"}, {"work_order_number": "WO-00003"}]},
    })
    assert _next_work_order_number() == "WO-00100"


def test_next_work_order_number_from_empty_table(patch_supabase):
    patch_supabase({"work_orders": {"select_return": []}})
    assert _next_work_order_number() == "WO-00001"


def test_next_work_order_number_ignores_unparseable_numbers(patch_supabase):
    patch_supabase({"work_orders": {"select_return": [{"work_order_number": None}, {"work_order_number": "WO-00005"}]}})
    assert _next_work_order_number() == "WO-00006"


# ─── _generate_one ────────────────────────────────────────────────────────────────

def _schedule(**overrides):
    base = {
        "id": 1, "name": "Compressor daily check", "equipment_info": "Compressor #3",
        "to_department": "Engineering", "allocated_to": "J. Moyo", "authorising_foreman": "T. Ncube",
        "estimated_hours": "2", "job_request_details": "Check oil level", "job_instructions": "See SOP-12",
        "priority": "medium", "recurrence_type": "daily", "next_due_date": date(2026, 8, 1),
        "active": True,
    }
    base.update(overrides)
    return base


def test_generate_one_happy_path_raises_work_order_and_rolls_schedule_forward(patch_supabase):
    s = _schedule()
    state = patch_supabase({
        "maintenance_schedule_runs": {"insert_return": lambda p, f: [{"id": 10, **p}]},
        "work_orders": {
            "select_return": [{"work_order_number": "WO-00003"}],
            "insert_return": lambda p, f: [{"id": 55, **p}],
        },
    })

    result = _generate_one(s, date(2026, 8, 1))

    assert result == {
        "schedule_id": 1, "due_date": "2026-08-01", "work_order_id": 55,
        "work_order_number": "WO-00004", "next_due_date": "2026-08-02",  # daily -> +1 day
    }

    claim = _calls(state, "maintenance_schedule_runs", "insert")[0]
    assert claim["payload"] == {"schedule_id": 1, "due_date": "2026-08-01"}

    wo_payload = _calls(state, "work_orders", "insert")[0]["payload"]
    assert wo_payload["title"] == "Compressor daily check"
    assert wo_payload["equipment_info"] == "Compressor #3"
    assert wo_payload["priority"] == "medium"
    assert wo_payload["status"] == "pending"
    assert wo_payload["due_date"] == "2026-08-01"
    assert wo_payload["requested_by"] == "Scheduled maintenance"
    assert "schedule #1" in wo_payload["notes"]

    run_update = _calls(state, "maintenance_schedule_runs", "update")[0]
    assert run_update["payload"] == {"work_order_id": 55}

    schedule_update = _calls(state, "maintenance_schedules", "update")[0]
    assert schedule_update["payload"]["next_due_date"] == "2026-08-02"
    assert "last_generated" in schedule_update["payload"]
    assert ("id", 1) in schedule_update["filters"]


def test_generate_one_already_claimed_skips_without_raising_a_work_order(patch_supabase):
    # UNIQUE(schedule_id, due_date) violation on the claim insert means a work order
    # already exists for this due date -> nothing new should be raised.
    s = _schedule()
    state = patch_supabase({
        "maintenance_schedule_runs": {"insert_raises": Exception('duplicate key value violates unique constraint')},
    })
    result = _generate_one(s, date(2026, 8, 1))
    assert result is None
    assert not _calls(state, "work_orders")


def test_generate_one_work_order_insert_failure_releases_the_claim_and_raises(patch_supabase):
    s = _schedule()
    state = patch_supabase({
        "maintenance_schedule_runs": {"insert_return": lambda p, f: [{"id": 10, **p}]},
        "work_orders": {
            "select_return": [],
            "insert_raises": Exception("insert failed"),
        },
    })
    with pytest.raises(Exception, match="insert failed"):
        _generate_one(s, date(2026, 8, 1))

    release = _calls(state, "maintenance_schedule_runs", "delete")
    assert len(release) == 1
    assert ("schedule_id", 1) in release[0]["filters"]
    assert ("due_date", "2026-08-01") in release[0]["filters"]
    # The schedule's next_due_date must NOT have been rolled forward on failure.
    assert not _calls(state, "maintenance_schedules", "update")


def test_generate_one_accepts_next_due_date_as_iso_string(patch_supabase):
    s = _schedule(next_due_date="2026-08-01")
    patch_supabase({
        "maintenance_schedule_runs": {"insert_return": lambda p, f: [{"id": 10, **p}]},
        "work_orders": {"select_return": [], "insert_return": lambda p, f: [{"id": 1, **p}]},
    })
    result = _generate_one(s, date(2026, 8, 1))
    assert result["due_date"] == "2026-08-01"


# ─── generate_due_work_orders — the endpoint that ties it together ──────────────

async def test_generate_cron_secret_bypasses_manager_auth(monkeypatch, patch_supabase):
    monkeypatch.setenv("CRON_SECRET", "s3cr3t")

    async def _boom(*a, **k):
        raise AssertionError("require_role should not be called when the cron secret matches")
    monkeypatch.setattr(sched_mod, "require_role", lambda role: _boom)

    patch_supabase({"maintenance_schedules": {"select_return": []}})
    result = await generate_due_work_orders(x_cron_secret="s3cr3t", authorization=None)
    assert result == {"generated": 0, "created": [], "failed": []}


async def test_generate_wrong_cron_secret_falls_back_to_manager_auth(monkeypatch, patch_supabase):
    monkeypatch.setenv("CRON_SECRET", "s3cr3t")
    called = {}

    async def _check(authorization):
        called["authorization"] = authorization
        return {"user_id": "manager-1", "role": "manager"}
    monkeypatch.setattr(sched_mod, "require_role", lambda role: _check)

    patch_supabase({"maintenance_schedules": {"select_return": []}})
    result = await generate_due_work_orders(x_cron_secret="wrong", authorization="Bearer good-token")
    assert result == {"generated": 0, "created": [], "failed": []}
    assert called["authorization"] == "Bearer good-token"


async def test_generate_insufficient_role_propagates_403(monkeypatch, patch_supabase):
    monkeypatch.delenv("CRON_SECRET", raising=False)

    async def _check(authorization):
        raise HTTPException(status_code=403, detail="Permission denied.")
    monkeypatch.setattr(sched_mod, "require_role", lambda role: _check)

    patch_supabase({})
    with pytest.raises(HTTPException) as exc:
        await generate_due_work_orders(x_cron_secret=None, authorization="Bearer weak-token")
    assert exc.value.status_code == 403


async def test_generate_schedules_load_failure_is_500(monkeypatch, patch_supabase):
    monkeypatch.setenv("CRON_SECRET", "s3cr3t")
    patch_supabase({"maintenance_schedules": {"select_raises": Exception("db down")}})
    with pytest.raises(HTTPException) as exc:
        await generate_due_work_orders(x_cron_secret="s3cr3t", authorization=None)
    assert exc.value.status_code == 500


async def test_generate_mixed_batch_only_generates_the_schedules_actually_due(monkeypatch, patch_supabase):
    """The end-to-end scenario the task explicitly asked for: a realistic mixed set of
    schedules — due, not-yet-due, inactive, already-generated-for-today, and one whose
    work-order insert blows up — confirming only the right ones actually produce a
    work order, and that one bad schedule doesn't stop the rest of the run."""
    monkeypatch.setenv("CRON_SECRET", "s3cr3t")
    today = date(2026, 8, 30)

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return today
    monkeypatch.setattr(sched_mod, "date", _FixedDate)  # generate_due_work_orders calls date.today() itself

    due_ok = {
        "id": 1, "name": "Due and healthy", "recurrence_type": "daily",
        "next_due_date": today, "active": True, "advance_days": 0,
    }
    not_due_yet = {
        "id": 2, "name": "Not due yet", "recurrence_type": "monthly", "recurrence_dom": 1,
        "next_due_date": date(2026, 9, 15), "active": True, "advance_days": 0,
    }
    inactive_but_would_be_due = {
        "id": 3, "name": "Inactive", "recurrence_type": "daily",
        "next_due_date": date(2026, 8, 1), "active": False, "advance_days": 0,
    }
    already_generated_today = {
        "id": 4, "name": "Already generated", "recurrence_type": "daily",
        "next_due_date": today, "active": True, "advance_days": 0,
    }
    broken_schedule = {
        "id": 5, "name": "Broken Schedule", "recurrence_type": "daily",
        "next_due_date": today, "active": True, "advance_days": 0,
    }

    def runs_insert(payload, filters):
        if payload.get("schedule_id") == 4:
            raise Exception("duplicate key value violates unique constraint")
        return [{"id": 99, **payload}]

    def wo_insert(payload, filters):
        if payload.get("title") == "Broken Schedule":
            raise Exception("simulated DB failure")
        return [{"id": 200 + len(payload["title"]), **payload}]

    state = patch_supabase({
        "maintenance_schedules": {
            "select_return": [due_ok, not_due_yet, inactive_but_would_be_due, already_generated_today, broken_schedule],
        },
        "maintenance_schedule_runs": {"insert_return": runs_insert},
        "work_orders": {"select_return": [], "insert_return": wo_insert},
    })

    result = await generate_due_work_orders(x_cron_secret="s3cr3t", authorization=None)

    assert result["generated"] == 1
    assert [c["schedule_id"] for c in result["created"]] == [1]
    assert len(result["failed"]) == 1
    assert result["failed"][0]["schedule_id"] == 5

    # The schedule that actually generated must have been rolled forward.
    schedule_updates = [c for c in _calls(state, "maintenance_schedules", "update")]
    assert len(schedule_updates) == 1
    assert ("id", 1) in schedule_updates[0]["filters"]

    # The broken schedule's claim must have been released, not left dangling.
    releases = _calls(state, "maintenance_schedule_runs", "delete")
    assert any(("schedule_id", 5) in r["filters"] for r in releases)
