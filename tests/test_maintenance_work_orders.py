# tests/test_maintenance_work_orders.py — the work-order CRUD endpoints in
# maintenance.py (get/create/update/delete/list-by-allocated) had zero tests despite
# real business logic: create_work_order's title/description/department/equipment
# fallback-from-other-fields, the manpower-None->[] guard, the server-side WO-number
# retry-on-collision loop actually wired end-to-end (not just the pure helpers already
# covered in test_work_order_number.py), and update_work_order's exclude_unset
# null-clearing. Uses the sanctioned "call the route coroutine directly against a fake
# supabase client" recipe.

import pytest

import app.routers.maintenance as m
from app.routers.maintenance import (
    WorkOrderCreate, WorkOrderUpdate, JobType,
    get_work_orders, create_work_order, get_work_order, update_work_order,
    delete_work_order, get_work_orders_by_allocated,
)
from fastapi import HTTPException


# ─── Broken-redis fixture: keeps get_work_orders' manual cache_get/cache_set and
# invalidate_namespace() calls fast + deterministic, without a real Redis. ──────────

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


# ─── Generic fake supabase: an in-memory table keyed by name, supporting the
# select/eq/order/limit/insert/update/delete chain every one of these endpoints uses. ──

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state):
        self.table_name = table_name
        self.state = state
        self._filters = []
        self._order = None
        self._limit = None
        self._mode = "select"
        self._payload = None

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
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
            next_id = self.state.setdefault("_next_id", [1])
            row["id"] = next_id[0]
            next_id[0] += 1
            table.append(row)
            return _Resp([row])

        matches = [r for r in table if all(r.get(c) == v for c, v in self._filters)]

        if self._mode == "select":
            if self._order:
                col, desc = self._order
                matches = sorted(matches, key=lambda r: r.get(col) or "", reverse=desc)
            if self._limit:
                matches = matches[: self._limit]
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

    def _patch(work_orders=None):
        state["work_orders"] = list(work_orders or [])
        monkeypatch.setattr(m, "supabase", _FakeSupabase(state))
        return state

    return _patch


def _user():
    return {"user_id": "u-1", "email": "u@x.com", "role": "user"}


def _manager():
    return {"user_id": "m-1", "email": "m@x.com", "role": "manager"}


def _wo(**overrides):
    """A full, valid WorkOrderCreate payload — every field is required by the model
    except the small handful marked Optional, so build one baseline and override."""
    base = dict(
        to_department="Engineering", to_section="Fitters", date_raised="2026-08-01",
        work_order_number="WO-CLIENT-GUESS", from_department="Mining", from_section="Ops",
        time_raised="08:00", account_number="ACC-1", equipment_info="Crusher 3",
        user_lab_today="Yes",
        job_type=JobType(maintenance=True), job_request_details="Bearing replacement needed",
        requested_by="J. Moyo", authorising_foreman="F. Ncube", authorising_engineer="E. Sibanda",
        allocated_to="T. Banda", estimated_hours="4", responsible_foreman="F. Ncube",
        job_instructions="Replace bearing on drive shaft",
        work_done_details="", cause_of_failure="", delay_details="",
        artisan_name="", artisan_sign="", artisan_date="",
        foreman_name="", foreman_sign="", foreman_date="",
        time_work_started="", time_work_finished="", total_time_worked="",
        overtime_start_time="", overtime_end_time="", overtime_hours="",
        delay_from_time="", delay_to_time="", total_delay_hours="",
    )
    base.update(overrides)
    return WorkOrderCreate(**base)


# ─── get_work_orders ────────────────────────────────────────────────────────────────

async def test_get_work_orders_no_filters_returns_all_sorted_desc(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "pending", "priority": "low", "created_at": "2026-08-01T00:00:00"},
        {"id": 2, "status": "pending", "priority": "low", "created_at": "2026-08-03T00:00:00"},
    ])
    result = await get_work_orders(
        status=None, priority=None, department=None, allocated_to=None, to_department=None, limit=None,
    )
    assert [r["id"] for r in result] == [2, 1]  # most recent first


async def test_get_work_orders_status_filter_excludes_others(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "pending", "priority": "low", "created_at": "2026-08-01T00:00:00"},
        {"id": 2, "status": "completed", "priority": "low", "created_at": "2026-08-02T00:00:00"},
    ])
    result = await get_work_orders(
        status="completed", priority=None, department=None, allocated_to=None, to_department=None, limit=None,
    )
    assert [r["id"] for r in result] == [2]


async def test_get_work_orders_status_all_is_not_filtered(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "pending", "priority": "low", "created_at": "2026-08-01T00:00:00"},
        {"id": 2, "status": "completed", "priority": "low", "created_at": "2026-08-02T00:00:00"},
    ])
    result = await get_work_orders(
        status="all", priority=None, department=None, allocated_to=None, to_department=None, limit=None,
    )
    assert len(result) == 2


async def test_get_work_orders_priority_department_allocated_to_department_filters(patch_supabase):
    patch_supabase([
        {"id": 1, "priority": "urgent", "department": "Eng", "allocated_to": "A", "to_department": "X",
         "created_at": "2026-08-01T00:00:00"},
        {"id": 2, "priority": "low", "department": "Ops", "allocated_to": "B", "to_department": "Y",
         "created_at": "2026-08-02T00:00:00"},
    ])
    assert [r["id"] for r in await get_work_orders(
        status=None, priority="urgent", department=None, allocated_to=None, to_department=None, limit=None,
    )] == [1]
    assert [r["id"] for r in await get_work_orders(
        status=None, priority=None, department="Ops", allocated_to=None, to_department=None, limit=None,
    )] == [2]
    assert [r["id"] for r in await get_work_orders(
        status=None, priority=None, department=None, allocated_to="B", to_department=None, limit=None,
    )] == [2]
    assert [r["id"] for r in await get_work_orders(
        status=None, priority=None, department=None, allocated_to=None, to_department="X", limit=None,
    )] == [1]


async def test_get_work_orders_wraps_query_failure_as_500(monkeypatch):
    class _BrokenQuery:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def order(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): raise Exception("db exploded")

    class _BrokenSupabase:
        def table(self, name): return _BrokenQuery()

    monkeypatch.setattr(m, "supabase", _BrokenSupabase())
    with pytest.raises(HTTPException) as exc:
        await get_work_orders(
            status=None, priority=None, department=None, allocated_to=None, to_department=None, limit=None,
        )
    assert exc.value.status_code == 500


async def test_get_work_orders_respects_limit(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "pending", "priority": "low", "created_at": "2026-08-01T00:00:00"},
        {"id": 2, "status": "pending", "priority": "low", "created_at": "2026-08-02T00:00:00"},
        {"id": 3, "status": "pending", "priority": "low", "created_at": "2026-08-03T00:00:00"},
    ])
    result = await get_work_orders(
        status=None, priority=None, department=None, allocated_to=None, to_department=None, limit=2,
    )
    assert len(result) == 2
    assert [r["id"] for r in result] == [3, 2]


async def test_get_work_orders_second_call_is_served_from_cache(monkeypatch, patch_supabase):
    # Use a working (not broken) in-memory fake redis for just this test, so the
    # cache_get/cache_set path in get_work_orders actually round-trips.
    from app import cache as cache_mod

    class _WorkingRedis:
        def __init__(self):
            self.store = {}
        async def get(self, key): return self.store.get(key)
        async def set(self, key, value, ex=None): self.store[key] = value
        async def sadd(self, key, *values): pass
        async def smembers(self, key): return set()
        async def delete(self, *keys): pass

    monkeypatch.setattr(cache_mod, "redis_client", _WorkingRedis())
    # Other tests in this session force the circuit breaker open via a broken fake
    # redis; reset it so this test's working fake is actually consulted.
    monkeypatch.setattr(cache_mod, "_redis_down_until", 0.0)
    state = patch_supabase([{"id": 1, "status": "pending", "created_at": "2026-08-01T00:00:00"}])

    first = await get_work_orders(
        status=None, priority=None, department=None, allocated_to=None, to_department=None, limit=None,
    )
    calls_after_first = len(state.get("calls", []))
    second = await get_work_orders(
        status=None, priority=None, department=None, allocated_to=None, to_department=None, limit=None,
    )
    assert second == first
    # No new supabase calls were made on the second, cache-served call.
    assert len(state.get("calls", [])) == calls_after_first


async def test_get_work_orders_parses_json_string_fields_in_response(patch_supabase):
    patch_supabase([
        {"id": 1, "status": "pending", "priority": "low", "created_at": "2026-08-01T00:00:00",
         "job_type": '{"maintenance": true}'},
    ])
    result = await get_work_orders(
        status=None, priority=None, department=None, allocated_to=None, to_department=None, limit=None,
    )
    assert result[0]["job_type"] == {"maintenance": True}


# ─── create_work_order ──────────────────────────────────────────────────────────────

async def test_create_work_order_fills_defaults_from_other_fields(patch_supabase):
    state = patch_supabase([])
    result = await create_work_order(_wo(), current_user=_user())
    assert result["title"] == "Bearing replacement needed"
    assert result["description"] == "Bearing replacement needed"
    assert result["department"] == "Engineering"       # falls back to to_department
    assert result["equipment"] == "Crusher 3"           # falls back to equipment_info
    assert result["manpower"] == []                     # None -> [] guard
    assert result["work_order_number"] == "WO-00001"    # server-allocated, table was empty


async def test_create_work_order_truncates_long_job_request_details_for_title(patch_supabase):
    patch_supabase([])
    long_details = "x" * 60
    result = await create_work_order(_wo(job_request_details=long_details), current_user=_user())
    assert result["title"] == "x" * 50 + "..."
    assert result["description"] == long_details        # description keeps the full text


async def test_create_work_order_explicit_title_is_not_overridden(patch_supabase):
    patch_supabase([])
    result = await create_work_order(_wo(title="Custom Title"), current_user=_user())
    assert result["title"] == "Custom Title"


# ─── create_work_order — the WO-number retry-on-collision loop, end to end ──────────

class _RetryTable:
    """work_orders table whose insert() raises a unique-violation `fail_times` times
    before succeeding — exercises create_work_order's actual retry loop (not just the
    pure helpers in test_work_order_number.py)."""
    def __init__(self, state):
        self.state = state
        self._mode = None
        self._payload = None

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, *a, **k):
        return self

    def insert(self, data):
        self._mode = "insert"
        self._payload = data
        return self

    def execute(self):
        if self._mode == "select":
            return _Resp(self.state["existing"])
        self.state["attempts"] += 1
        if self.state["attempts"] <= self.state["fail_times"]:
            raise Exception('duplicate key value violates unique constraint "uq_work_orders_number"')
        row = dict(self._payload, id=99)
        return _Resp([row])


class _RetrySupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _RetryTable(self.state)


async def test_create_work_order_retries_past_a_number_collision_then_succeeds(monkeypatch):
    state = {"existing": [], "attempts": 0, "fail_times": 2}
    monkeypatch.setattr(m, "supabase", _RetrySupabase(state))
    result = await create_work_order(_wo(), current_user=_user())
    assert state["attempts"] == 3          # failed twice, succeeded on the 3rd
    assert result["work_order_number"] == "WO-00003"  # offset=2 by the time it succeeded


async def test_create_work_order_exhausts_retries_returns_409(monkeypatch):
    # Regression test for a live bug: the post-loop 409 branch was unreachable because
    # the final attempt (attempt == 5) always re-raised the raw duplicate-key exception
    # instead of falling through — surfacing an opaque 500 instead of a graceful 409.
    # Fixed in this pass (see maintenance.py's create_work_order retry loop).
    state = {"existing": [], "attempts": 0, "fail_times": 999}  # always collides
    monkeypatch.setattr(m, "supabase", _RetrySupabase(state))
    with pytest.raises(HTTPException) as exc:
        await create_work_order(_wo(), current_user=_user())
    assert exc.value.status_code == 409
    assert state["attempts"] == 6          # attempts 0..5, all failed


# ─── get_work_order ─────────────────────────────────────────────────────────────────

async def test_get_work_order_found(patch_supabase):
    patch_supabase([{"id": 5, "status": "pending", "job_type": '{"maintenance": true}'}])
    result = await get_work_order(5)
    assert result["id"] == 5
    assert result["job_type"] == {"maintenance": True}


async def test_get_work_order_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await get_work_order(999)
    assert exc.value.status_code == 404


# ─── update_work_order ──────────────────────────────────────────────────────────────

async def test_update_work_order_happy_path_changes_only_sent_fields(patch_supabase):
    state = patch_supabase([{"id": 7, "status": "pending", "priority": "low"}])
    result = await update_work_order(7, WorkOrderUpdate(status="completed"), current_user=_user())
    assert result["status"] == "completed"
    assert result["priority"] == "low"  # untouched
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert "priority" not in update_calls[0]["payload"]  # unset field never sent


async def test_update_work_order_explicit_null_clears_the_field(patch_supabase):
    state = patch_supabase([{"id": 7, "status": "pending", "due_date": "2026-09-01"}])
    result = await update_work_order(7, WorkOrderUpdate(due_date=None), current_user=_user())
    assert result["due_date"] is None
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert update_calls[0]["payload"]["due_date"] is None  # explicitly sent, not dropped


async def test_update_work_order_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await update_work_order(404, WorkOrderUpdate(status="completed"), current_user=_user())
    assert exc.value.status_code == 404


# ─── delete_work_order ──────────────────────────────────────────────────────────────

async def test_delete_work_order_happy_path(patch_supabase):
    state = patch_supabase([{"id": 3, "status": "pending"}])
    result = await delete_work_order(3, current_user=_manager())
    assert result == {"success": True, "message": "Work order deleted successfully"}
    assert state["work_orders"] == []


async def test_delete_work_order_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await delete_work_order(404, current_user=_manager())
    assert exc.value.status_code == 404


# ─── get_work_orders_by_allocated ───────────────────────────────────────────────────

async def test_get_work_orders_by_allocated_filters_and_sorts(patch_supabase):
    patch_supabase([
        {"id": 1, "allocated_to": "T. Banda", "created_at": "2026-08-01T00:00:00"},
        {"id": 2, "allocated_to": "Other Guy", "created_at": "2026-08-02T00:00:00"},
        {"id": 3, "allocated_to": "T. Banda", "created_at": "2026-08-05T00:00:00"},
    ])
    result = await get_work_orders_by_allocated("T. Banda")
    assert [r["id"] for r in result] == [3, 1]
