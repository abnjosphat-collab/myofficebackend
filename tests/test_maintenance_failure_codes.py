# tests/test_maintenance_failure_codes.py - M3: Problem / Cause / Action failure
# coding on breakdown closes. Same recipe as test_maintenance_close.py: call
# the route coroutine directly against a fake supabase client.
import pytest
from fastapi import HTTPException

import app.routers.maintenance as m
from app.routers.maintenance import (
    WorkOrderClose, WorkOrderUpdate,
    close_problems, close_work_order, update_work_order,
)


class _BrokenRedis:
    async def get(self, *a, **k): raise ConnectionError("no redis in tests")
    async def set(self, *a, **k): raise ConnectionError("no redis in tests")
    async def sadd(self, *a, **k): raise ConnectionError("no redis in tests")
    async def smembers(self, *a, **k): raise ConnectionError("no redis in tests")
    async def delete(self, *a, **k): raise ConnectionError("no redis in tests")


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    from app import cache as cache_mod
    monkeypatch.setattr(cache_mod, "redis_client", _BrokenRedis())


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state):
        self.table_name = table_name
        self.state = state
        self._filters = []
        self._mode = "select"
        self._payload = None

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def ilike(self, col, val):
        self._filters.append(("ilike", col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def insert(self, data):
        self._mode = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._mode = "update"
        self._payload = data
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def execute(self):
        table = self.state.setdefault(self.table_name, [])
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "mode": self._mode, "filters": list(self._filters), "payload": self._payload}
        )
        if self._mode == "insert":
            row = dict(self._payload)
            row["id"] = len(table) + 1
            table.append(row)
            return _Resp([row])
        def _hit(r, f):
            if len(f) == 3:  # ilike: case-insensitive match
                return str(r.get(f[1]) or "").lower() == str(f[2]).lower()
            return r.get(f[0]) == f[1]

        matches = [r for r in table if all(_hit(r, f) for f in self._filters)]
        if self._mode == "select":
            return _Resp(matches)
        if self._mode == "update":
            for r in matches:
                r.update(self._payload)
            return _Resp(matches)
        if self._mode == "delete":
            for r in matches:
                table.remove(r)
            return _Resp(matches)
        return _Resp([])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(name, self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {}

    def _patch(work_orders=None, lookup_lists=None, columns=True):
        state["work_orders"] = list(work_orders or [])
        state["lookup_lists"] = list(lookup_lists or [])
        monkeypatch.setattr(m, "supabase", _FakeSupabase(state))
        # Pin the column probe (it caches YES process-wide; see M1 notes).
        monkeypatch.setattr(m, "has_columns", lambda *a, **k: columns)
        return state

    return _patch


def _user(**over):
    base = {"user_id": "u-1", "email": "artisan@mine.test", "role": "user"}
    base.update(over)
    return base


def _row(**over):
    base = {
        "id": 1, "work_order_number": "WO-00001", "equipment_info": "Pump A",
        "status": "pending", "priority": "medium", "progress": 0,
        "classification": None, "work_done_details": "", "cause_of_failure": "",
        "failure_mode": "", "corrective_action": "", "artisan_name": "",
        "artisan_date": "", "time_work_started": "", "time_work_finished": "",
        "total_time_worked": "", "foreman_sign": "",
        "updated_at": "2026-10-26T08:00:00+00:00",
    }
    base.update(over)
    return base


def _updates(state):
    return [c["payload"] for c in state["calls"] if c["table"] == "work_orders" and c["mode"] == "update"]


def _learned(state):
    return [(c["payload"]["list_name"], c["payload"]["value"])
            for c in state["calls"] if c["table"] == "lookup_lists" and c["mode"] == "insert"]


def _breakdown_row(**over):
    base = dict(work_done_details="Done.", total_time_worked="1h 00m", classification="breakdown")
    base.update(over)
    return _row(**base)


# --- the rule: Action required only for breakdowns, only once stored ---

def test_breakdown_close_needs_the_action_code():
    row = _breakdown_row(cause_of_failure="Seal worn", failure_mode="Seal failure")
    assert close_problems(row, WorkOrderClose()) == ["Say what action fixed it."]
    assert close_problems(row, WorkOrderClose(corrective_action="Replaced seal")) == []


def test_non_breakdown_close_needs_no_codes():
    row = _row(work_done_details="Done.", total_time_worked="1h 00m",
               classification="planned_maintenance")
    assert close_problems(row, WorkOrderClose()) == []


def test_action_already_on_the_row_counts():
    row = _breakdown_row(cause_of_failure="c", failure_mode="m", corrective_action="a")
    assert close_problems(row, WorkOrderClose()) == []


def test_action_not_required_before_the_m3_sql_runs():
    row = _breakdown_row(cause_of_failure="c", failure_mode="m")
    assert close_problems(row, WorkOrderClose(), require_action=False) == []


# --- POST /work-orders/{id}/close ---

async def test_close_breakdown_stores_the_action_and_learns_the_codes(patch_supabase):
    state = patch_supabase([_breakdown_row()])
    saved = await close_work_order(1, WorkOrderClose(
        cause_of_failure="Seal worn", failure_mode="Bearing failure",
        corrective_action="Replaced seal"), _user())
    assert saved["status"] == "completed"
    assert saved["corrective_action"] == "Replaced seal"
    assert _updates(state)[0]["corrective_action"] == "Replaced seal"
    assert sorted(_learned(state)) == [
        ("failure_action", "Replaced seal"),
        ("failure_cause", "Seal worn"),
        ("failure_problem", "Bearing failure"),
    ]


async def test_close_breakdown_without_action_is_422(patch_supabase):
    patch_supabase([_breakdown_row()])
    with pytest.raises(HTTPException) as exc:
        await close_work_order(1, WorkOrderClose(
            cause_of_failure="Seal worn", failure_mode="Bearing failure"), _user())
    assert exc.value.status_code == 422
    assert "what action fixed it" in exc.value.detail


async def test_close_learns_nothing_already_on_the_list(patch_supabase):
    state = patch_supabase(
        [_breakdown_row()],
        lookup_lists=[
            {"id": 1, "list_name": "failure_problem", "value": "bearing FAILURE"},
            {"id": 2, "list_name": "failure_cause", "value": "Seal worn"},
            {"id": 3, "list_name": "failure_action", "value": "Replaced seal"},
        ],
    )
    await close_work_order(1, WorkOrderClose(
        cause_of_failure="seal WORN", failure_mode="Bearing failure",
        corrective_action="replaced SEAL"), _user())
    assert _learned(state) == []  # case-insensitive matches are left alone


async def test_close_pre_sql_neither_requires_nor_stores_nor_learns_action(patch_supabase):
    state = patch_supabase([_breakdown_row()], columns=False)
    saved = await close_work_order(1, WorkOrderClose(
        cause_of_failure="Seal worn", failure_mode="Bearing failure",
        corrective_action="Replaced seal"), _user())
    assert saved["status"] == "completed"
    assert "corrective_action" not in _updates(state)[0]
    assert ("failure_action", "Replaced seal") not in _learned(state)


def test_learn_survives_a_dead_lookup_store(patch_supabase, monkeypatch):
    patch_supabase([_breakdown_row()])

    class _Dead:
        def table(self, _name):
            raise ConnectionError("lookup store down")

    monkeypatch.setattr(m, "supabase", _Dead())
    # No raise: learning a code never fails the write it came with.
    m._learn_failure_codes(
        {"failure_mode": "m", "cause_of_failure": "c", "corrective_action": "a"}, True)


# --- PATCH /work-orders/{id} (the artisan report path) ---

async def test_patch_stores_action_and_learns_codes(patch_supabase):
    state = patch_supabase([_breakdown_row()])
    saved = await update_work_order(1, WorkOrderUpdate(
        failure_mode="Bearing failure", cause_of_failure="Seal worn",
        corrective_action="Replaced seal"), _user())
    assert saved["corrective_action"] == "Replaced seal"
    assert ("failure_problem", "Bearing failure") in _learned(state)
    assert ("failure_cause", "Seal worn") in _learned(state)
    assert ("failure_action", "Replaced seal") in _learned(state)


async def test_patch_pre_sql_drops_action_but_learns_problem_and_cause(patch_supabase):
    state = patch_supabase([_breakdown_row()], columns=False)
    saved = await update_work_order(1, WorkOrderUpdate(
        failure_mode="Bearing failure", cause_of_failure="Seal worn",
        corrective_action="Replaced seal"), _user())
    assert "corrective_action" not in _updates(state)[0]
    assert saved["failure_mode"] == "Bearing failure"
    assert ("failure_problem", "Bearing failure") in _learned(state)
    assert ("failure_action", "Replaced seal") not in _learned(state)
