# tests/test_maintenance_bulk_delete.py - B0: bulk delete plus the single-delete
# run-link fix. Same recipe as test_maintenance_close.py: call the route
# coroutine directly against a fake supabase client. Role gating through the
# real HTTP stack (401/403) follows test_tasks_events_router.py.
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.routers.maintenance as m
from app.routers.maintenance import (
    WorkOrderBulkDelete,
    bulk_delete_work_orders,
    delete_work_order,
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
        if self.table_name in self.state.get("_explode", ()):
            raise Exception("db exploded")
        if self._mode == "delete" and any(
                c == "id" and v in self.state.get("_fail_delete_ids", ()) for c, v in self._filters):
            raise Exception("db exploded on delete")
        table = self.state.setdefault(self.table_name, [])
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "mode": self._mode, "filters": list(self._filters), "payload": self._payload}
        )
        if self._mode == "insert":
            row = dict(self._payload)
            row["id"] = len(table) + 1
            table.append(row)
            return _Resp([row])
        matches = [r for r in table if all(r.get(c) == v for c, v in self._filters)]
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

    def _patch(work_orders=None, runs=None, explode=(), fail_delete_ids=()):
        state["work_orders"] = list(work_orders or [])
        state["maintenance_schedule_runs"] = list(runs or [])
        state["_explode"] = tuple(explode)
        state["_fail_delete_ids"] = tuple(fail_delete_ids)
        monkeypatch.setattr(m, "supabase", _FakeSupabase(state))
        return state

    return _patch


def _manager(**over):
    base = {"user_id": "m-1", "email": "manager@mine.test", "role": "manager"}
    base.update(over)
    return base


def _row(**over):
    base = {
        "id": 1, "work_order_number": "WO-00001", "equipment_info": "Pump A",
        "status": "pending", "priority": "medium", "progress": 0,
        "section": "Mechanical",
    }
    base.update(over)
    return base


def _run(**over):
    base = {"id": 1, "schedule_id": 1, "due_date": "2026-10-20", "work_order_id": None}
    base.update(over)
    return base


# --- POST /work-orders/bulk-delete ---

async def test_bulk_delete_happy_path_deletes_and_reports_per_row(patch_supabase):
    state = patch_supabase([_row(id=1), _row(id=2, status="in-progress")])
    result = await bulk_delete_work_orders(WorkOrderBulkDelete(ids=[1, 2]), _manager())
    assert result == {"deleted": [1, 2], "failed": []}
    assert state["work_orders"] == []


async def test_bulk_delete_writes_one_event_per_deleted_row(patch_supabase):
    state = patch_supabase([
        _row(id=1, work_order_number="WO-00001", status="pending"),
        _row(id=2, work_order_number="WO-00002", status="in-progress"),
    ])
    await bulk_delete_work_orders(WorkOrderBulkDelete(ids=[1, 2]), _manager())
    events = state["maintenance_events"]
    assert [(e["action"], e["entity_id"]) for e in events] == [("deleted", 1), ("deleted", 2)]
    assert events[0]["entity_number"] == "WO-00001"
    assert events[0]["from_status"] == "pending"
    assert events[1]["from_status"] == "in-progress"


async def test_bulk_delete_clears_run_links_and_keeps_run_rows(patch_supabase):
    state = patch_supabase(
        [_row(id=1), _row(id=2)],
        runs=[
            _run(id=10, work_order_id=1),
            _run(id=11, work_order_id=1),
            _run(id=12, work_order_id=9),
        ],
    )
    result = await bulk_delete_work_orders(WorkOrderBulkDelete(ids=[1, 2]), _manager())
    assert result == {"deleted": [1, 2], "failed": []}
    runs = {r["id"]: r["work_order_id"] for r in state["maintenance_schedule_runs"]}
    assert runs == {10: None, 11: None, 12: 9}


async def test_bulk_delete_missing_id_is_reported_not_deleted(patch_supabase):
    patch_supabase([_row(id=1)])
    result = await bulk_delete_work_orders(WorkOrderBulkDelete(ids=[1, 99]), _manager())
    assert result["deleted"] == [1]
    assert result["failed"] == [{"id": 99, "reason": "It no longer exists."}]


async def test_bulk_delete_unreadable_row_is_reported_and_rest_deleted(patch_supabase):
    state = patch_supabase([_row(id=1), _row(id=2)], explode=("work_orders",))
    result = await bulk_delete_work_orders(WorkOrderBulkDelete(ids=[1, 2]), _manager())
    assert result["deleted"] == []
    assert result["failed"] == [
        {"id": 1, "reason": "It could not be read; try again."},
        {"id": 2, "reason": "It could not be read; try again."},
    ]
    assert [e["action"] for e in state.get("maintenance_events", [])] == []


async def test_bulk_delete_write_error_skips_the_row_and_keeps_the_rest(patch_supabase):
    state = patch_supabase([_row(id=1), _row(id=2)], fail_delete_ids=(2,))
    result = await bulk_delete_work_orders(WorkOrderBulkDelete(ids=[1, 2]), _manager())
    assert result["deleted"] == [1]
    assert result["failed"] == [{"id": 2, "reason": "It could not be deleted; try again."}]
    assert [r["id"] for r in state["work_orders"]] == [2]
    assert [(e["action"], e["entity_id"]) for e in state["maintenance_events"]] == [("deleted", 1)]


async def test_bulk_delete_scoped_manager_cannot_delete_out_of_scope(patch_supabase):
    state = patch_supabase([_row(id=1, section="Mechanical"), _row(id=2, section="Projects")])
    result = await bulk_delete_work_orders(
        WorkOrderBulkDelete(ids=[1, 2]), _manager(scope="Projects"))
    assert result["deleted"] == [2]
    assert result["failed"] == [{"id": 1, "reason": "You can no longer see it."}]
    assert [r["id"] for r in state["work_orders"]] == [1]


def test_bulk_delete_ids_are_capped():
    with pytest.raises(ValidationError):
        WorkOrderBulkDelete(ids=[])
    with pytest.raises(ValidationError):
        WorkOrderBulkDelete(ids=list(range(101)))
    assert WorkOrderBulkDelete(ids=[1, 2]).ids == [1, 2]


# --- single DELETE clears the run link ---

async def test_single_delete_clears_run_links_and_keeps_run_rows(patch_supabase):
    state = patch_supabase(
        [_row(id=3)],
        runs=[_run(id=10, work_order_id=3), _run(id=11, work_order_id=8)],
    )
    result = await delete_work_order(3, current_user=_manager())
    assert result == {"success": True, "message": "Work order deleted successfully"}
    assert state["work_orders"] == []
    runs = {r["id"]: r["work_order_id"] for r in state["maintenance_schedule_runs"]}
    assert runs == {10: None, 11: 8}


async def test_single_delete_missing_row_is_404_and_clears_nothing(patch_supabase):
    state = patch_supabase([], runs=[_run(id=10, work_order_id=404)])
    with pytest.raises(HTTPException) as exc:
        await delete_work_order(404, current_user=_manager())
    assert exc.value.status_code == 404
    assert state["maintenance_schedule_runs"] == [_run(id=10, work_order_id=404)]


# --- manager-only, through the real HTTP stack ---

class _FakeAuthClient:
    def __init__(self, user):
        self._user = user

    def get_user(self, token):
        class R:
            pass
        r = R()
        r.user = self._user
        return r


class _FakeRpcResult:
    def __init__(self, data):
        self.data = data


class _FakeAuthSupabase:
    def __init__(self, user, role):
        self.auth = _FakeAuthClient(user)
        self._role = role

    def rpc(self, name, params):
        return self

    def execute(self):
        return _FakeRpcResult(self._role)


class _FakeAuthUser:
    def __init__(self, uid="u-b0", email="tech@mine.test"):
        self.id = uid
        self.email = email
        self.factors = []


def test_bulk_delete_signed_in_non_manager_is_403(monkeypatch):
    from app import auth as auth_mod
    from main import app

    monkeypatch.setattr(auth_mod, "supabase", _FakeAuthSupabase(_FakeAuthUser(), role="user"))
    client = TestClient(app)
    resp = client.post(
        "/api/maintenance/work-orders/bulk-delete",
        json={"ids": [1]},
        headers={"Authorization": "Bearer user-token-b0"},
    )
    assert resp.status_code == 403, f"expected 403 for a signed-in non-manager, got {resp.status_code}: {resp.text}"
