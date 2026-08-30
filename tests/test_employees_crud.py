# tests/test_employees_crud.py — the six handlers left uncovered after
# test_employees_date_conversion.py (the _dates_to_db/_dates_from_db helpers) and
# test_employees_search.py (search_employees's field routing): employees_health,
# get_employees, get_employee, create_employee, update_employee, delete_employee.
# Covers the duplicate-employee_id guard on create, the employee_id-can-change-but-
# not-collide guard on update, the "Unknown" name fallback on delete, and every
# handler's generic-exception -> 500 path. Uses the sanctioned "call the route
# coroutine directly against a fake supabase client" recipe (documents.py's
# call-recording variant), with an explicit response queue since employees.py issues
# more than one distinct select against the same table per request (existence check,
# then employee_id clash check).

from datetime import date

import pytest
from fastapi import HTTPException

import app.routers.employees as emp_mod
from app.routers.employees import (
    Employee,
    employees_health, get_employees, get_employee,
    create_employee, update_employee, delete_employee,
)


# ─── Broken-redis fixture: get_employees is wrapped in @cached — keep it fast and
# deterministic without a real local Redis. ─────────────────────────────────────────

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


# ─── Fake supabase: records every call and serves responses off a queue, one per
# execute() call in the order the handler issues them. ──────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _RaisingResp:
    """Placeholder queue entry that makes execute() raise instead of return."""
    def __init__(self, exc):
        self.exc = exc


class _FakeQuery:
    def __init__(self, table_name, calls, queue):
        self.table_name = table_name
        self._calls = calls
        self._queue = queue
        self._filters = []
        self._op = "select"
        self._payload = None

    def select(self, *a, **k): return self
    def eq(self, col, val): self._filters.append(("eq", col, val)); return self
    def neq(self, col, val): self._filters.append(("neq", col, val)); return self
    def ilike(self, col, val): self._filters.append(("ilike", col, val)); return self
    def or_(self, expr): self._filters.append(("or_", expr)); return self
    def limit(self, n): return self

    def insert(self, data):
        self._op = "insert"; self._payload = data; return self

    def update(self, data):
        self._op = "update"; self._payload = data; return self

    def delete(self):
        self._op = "delete"; return self

    def execute(self):
        self._calls.append({
            "table": self.table_name, "op": self._op,
            "filters": list(self._filters), "payload": self._payload,
        })
        item = self._queue.pop(0)
        if isinstance(item, _RaisingResp):
            raise item.exc
        return _Resp(item)


class _FakeSupabase:
    def __init__(self, calls, queue):
        self._calls = calls
        self._queue = queue

    def table(self, name):
        return _FakeQuery(name, self._calls, self._queue)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(responses):
        calls = []
        monkeypatch.setattr(emp_mod, "supabase", _FakeSupabase(calls, list(responses)))
        return calls
    return _patch


def test_employee_id_validator_rejects_blank_id():
    with pytest.raises(Exception):
        _employee(employee_id="   ")


def test_employee_id_validator_strips_and_uppercases():
    e = _employee(employee_id=" c1165 ")
    assert e.employee_id == "C1165"


def _employee(**overrides):
    base = dict(
        employee_id="C1165",
        first_name="John",
        last_name="Doe",
        id_number="ID123",
        date_of_engagement=date(2020, 1, 1),
        designation="Technician",
    )
    base.update(overrides)
    return Employee(**base)


# ─── employees_health ───────────────────────────────────────────────────────────────

async def test_health_reports_healthy_on_success(patch_supabase):
    patch_supabase([[{"id": 1}]])
    result = await employees_health()
    assert result == {"status": "healthy", "service": "employees", "database": "connected"}


async def test_health_reports_503_on_db_failure(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("db down"))])
    with pytest.raises(HTTPException) as exc:
        await employees_health()
    assert exc.value.status_code == 503


# ─── get_employees ───────────────────────────────────────────────────────────────────

async def test_get_employees_converts_dates_on_every_row(patch_supabase):
    patch_supabase([[
        {"id": 1, "date_of_engagement": "2020-03-15"},
        {"id": 2, "date_of_engagement": "2021-06-01"},
    ]])
    result = await get_employees()
    assert result[0]["date_of_engagement"] == date(2020, 3, 15)
    assert result[1]["date_of_engagement"] == date(2021, 6, 1)


async def test_get_employees_empty_rows_is_empty_list(patch_supabase):
    patch_supabase([[]])
    assert await get_employees() == []


async def test_get_employees_db_failure_is_500(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("boom"))])
    with pytest.raises(HTTPException) as exc:
        await get_employees()
    assert exc.value.status_code == 500


# ─── get_employee ────────────────────────────────────────────────────────────────────

async def test_get_employee_found_converts_dates(patch_supabase):
    patch_supabase([[{"id": 5, "date_of_engagement": "2019-11-20"}]])
    result = await get_employee(5)
    assert result["date_of_engagement"] == date(2019, 11, 20)


async def test_get_employee_not_found_is_404(patch_supabase):
    patch_supabase([[]])
    with pytest.raises(HTTPException) as exc:
        await get_employee(999)
    assert exc.value.status_code == 404
    assert "999" in exc.value.detail


async def test_get_employee_db_failure_is_500(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("boom"))])
    with pytest.raises(HTTPException) as exc:
        await get_employee(5)
    assert exc.value.status_code == 500


# ─── create_employee ─────────────────────────────────────────────────────────────────

async def test_create_employee_rejects_duplicate_employee_id(patch_supabase):
    calls = patch_supabase([[{"id": 1}]])  # clash check returns a match
    with pytest.raises(HTTPException) as exc:
        await create_employee(_employee(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 400
    assert "C1165" in exc.value.detail
    # Only the clash-check select happened, no insert.
    assert all(c["op"] == "select" for c in calls)


async def test_create_employee_succeeds_and_converts_dates_back(patch_supabase):
    calls = patch_supabase([
        [],  # clash check: no existing row
        [{"id": 10, "employee_id": "C1165", "date_of_engagement": "2020-01-01"}],
    ])
    result = await create_employee(_employee(), current_user={"user_id": "u1"})
    assert result["id"] == 10
    assert result["date_of_engagement"] == date(2020, 1, 1)

    insert_call = [c for c in calls if c["op"] == "insert"][0]
    # date_of_engagement must be sent to Supabase as an ISO string, not a date object.
    assert insert_call["payload"]["date_of_engagement"] == "2020-01-01"


async def test_create_employee_no_rows_returned_is_500(patch_supabase):
    patch_supabase([
        [],  # no clash
        [],  # insert "succeeds" but Supabase returns nothing
    ])
    with pytest.raises(HTTPException) as exc:
        await create_employee(_employee(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_create_employee_db_failure_is_500(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("boom"))])
    with pytest.raises(HTTPException) as exc:
        await create_employee(_employee(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── update_employee ─────────────────────────────────────────────────────────────────

async def test_update_employee_not_found_is_404(patch_supabase):
    patch_supabase([[]])
    with pytest.raises(HTTPException) as exc:
        await update_employee(999, _employee(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 404


async def test_update_employee_same_employee_id_skips_clash_check(patch_supabase):
    calls = patch_supabase([
        [{"id": 5, "employee_id": "C1165"}],  # existence check: same employee_id
        [{"id": 5, "employee_id": "C1165", "date_of_engagement": "2020-01-01"}],  # update
    ])
    result = await update_employee(5, _employee(employee_id="C1165"), current_user={"user_id": "u1"})
    assert result["id"] == 5
    # No clash-check select fired (only existence-select + update = 2 calls).
    assert len(calls) == 2


async def test_update_employee_changed_id_rejects_collision(patch_supabase):
    patch_supabase([
        [{"id": 5, "employee_id": "OLD_ID"}],   # existence check
        [{"id": 7}],                             # clash check finds another employee
    ])
    with pytest.raises(HTTPException) as exc:
        await update_employee(5, _employee(employee_id="NEW_ID"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 400
    assert "NEW_ID" in exc.value.detail


async def test_update_employee_changed_id_succeeds_when_unique(patch_supabase):
    patch_supabase([
        [{"id": 5, "employee_id": "OLD_ID"}],  # existence check
        [],                                     # clash check: no collision
        [{"id": 5, "employee_id": "NEW_ID", "date_of_engagement": "2020-01-01"}],  # update
    ])
    result = await update_employee(5, _employee(employee_id="NEW_ID"), current_user={"user_id": "u1"})
    assert result["employee_id"] == "NEW_ID"


async def test_update_employee_no_rows_returned_is_500(patch_supabase):
    patch_supabase([
        [{"id": 5, "employee_id": "C1165"}],
        [],  # update "succeeds" but returns nothing
    ])
    with pytest.raises(HTTPException) as exc:
        await update_employee(5, _employee(employee_id="C1165"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_update_employee_db_failure_is_500(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("boom"))])
    with pytest.raises(HTTPException) as exc:
        await update_employee(5, _employee(), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── delete_employee ─────────────────────────────────────────────────────────────────

async def test_delete_employee_not_found_is_404(patch_supabase):
    patch_supabase([[]])
    with pytest.raises(HTTPException) as exc:
        await delete_employee(999, current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 404


async def test_delete_employee_success_reports_name_and_id(patch_supabase):
    calls = patch_supabase([
        [{"id": 5, "employee_id": "C1165", "first_name": "John", "last_name": "Doe"}],
        None,  # delete().execute() response, unused by the handler
    ])
    result = await delete_employee(5, current_user={"user_id": "u1", "role": "manager"})
    assert result == {"success": True, "detail": "John Doe (C1165) deleted", "deleted_id": 5}
    assert any(c["op"] == "delete" for c in calls)


async def test_delete_employee_blank_name_falls_back_to_unknown(patch_supabase):
    patch_supabase([
        [{"id": 5, "employee_id": "C1165", "first_name": "", "last_name": ""}],
        None,
    ])
    result = await delete_employee(5, current_user={"user_id": "u1", "role": "manager"})
    assert result["detail"] == "Unknown (C1165) deleted"


async def test_delete_employee_db_failure_is_500(patch_supabase):
    patch_supabase([_RaisingResp(RuntimeError("boom"))])
    with pytest.raises(HTTPException) as exc:
        await delete_employee(5, current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 500
