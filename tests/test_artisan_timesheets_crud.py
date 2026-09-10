import json
import pytest
from fastapi import HTTPException

from app.routers import artisan_timesheets as mod
from app.routers.artisan_timesheets import (
    ArtisanTimesheetCreate,
    ArtisanTimesheetUpdate,
    create_artisan_timesheet,
    list_artisan_timesheets,
    update_artisan_timesheet,
    delete_artisan_timesheet,
)


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, state, cfg):
        self.state = state
        self.cfg = cfg
        self._op = "select"
        self._filters = []
        self._payload = None
        self._orders = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._orders.append((col, desc))
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
            {"op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        if self.cfg.get("raise_op") == self._op:
            raise Exception(self.cfg.get("raise_msg", "boom"))
        if self._op == "insert":
            return _Resp(self.cfg.get("insert_return"))
        if self._op == "update":
            return _Resp(self.cfg.get("update_return"))
        if self._op == "delete":
            return _Resp(self.cfg.get("delete_return", [{"id": 1}]))
        return _Resp(self.cfg.get("select_return"))


class _FakeSupabase:
    def __init__(self, cfg):
        self.state = {"calls": []}
        self.cfg = cfg

    def table(self, name):
        assert name == "artisan_timesheets"
        return _Query(self.state, self.cfg)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _apply(cfg: dict) -> _FakeSupabase:
        fake = _FakeSupabase(cfg)
        monkeypatch.setattr(mod, "supabase", fake)
        return fake
    return _apply


CURRENT_USER = {"user_id": "u1", "email": "u1@x.com", "role": "user"}


async def test_list_artisan_timesheets_returns_decoded_rows(patch_supabase):
    patch_supabase({
        "select_return": [{
            "id": 1,
            "employee_id": "C001",
            "employee_name": "Alice",
            "year": 2024,
            "month": 1,
            "adjustment": 0,
            "daily_rows": json.dumps([{"date": "2024-01-01", "day": "Mon", "normal_hrs": 8}]),
        }],
    })
    result = await list_artisan_timesheets(employee_id="C001", year=2024, month=1, current_user=CURRENT_USER)
    assert len(result) == 1
    assert isinstance(result[0]["daily_rows"], list)


async def test_create_artisan_timesheet_happy_path(patch_supabase):
    fake = patch_supabase({
        "select_return": None,
        "insert_return": [{
            "id": 5,
            "employee_id": "C001",
            "employee_name": "Alice",
            "year": 2024,
            "month": 3,
            "adjustment": 0,
            "daily_rows": "[]",
        }],
    })
    body = ArtisanTimesheetCreate(
        employee_id="C001",
        employee_name="Alice",
        year=2024,
        month=3,
    )
    result = await create_artisan_timesheet(body, current_user=CURRENT_USER)
    assert result["id"] == 5
    insert_call = next(c for c in fake.state["calls"] if c["op"] == "insert")
    assert insert_call["payload"]["employee_id"] == "C001"


async def test_create_artisan_timesheet_duplicate_is_409(patch_supabase):
    patch_supabase({"select_return": [{"id": 9}]})
    with pytest.raises(HTTPException) as exc:
        await create_artisan_timesheet(
            ArtisanTimesheetCreate(employee_id="C001", employee_name="Alice", year=2024, month=3),
            current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 409


async def test_update_artisan_timesheet_happy_path(patch_supabase):
    fake = patch_supabase({
        "select_return": [{"id": 2, "employee_id": "C001", "employee_name": "Alice", "year": 2024, "month": 3, "adjustment": 0, "daily_rows": "[]"}],
        "update_return": [{
            "id": 2,
            "employee_id": "C001",
            "employee_name": "Alice",
            "year": 2024,
            "month": 3,
            "adjustment": 1.5,
            "daily_rows": "[]",
        }],
    })
    result = await update_artisan_timesheet(
        2,
        ArtisanTimesheetUpdate(adjustment=1.5, compiled_by="Clerk"),
        current_user=CURRENT_USER,
    )
    assert result["adjustment"] == 1.5
    update_call = next(c for c in fake.state["calls"] if c["op"] == "update")
    assert update_call["payload"]["compiled_by"] == "Clerk"


async def test_delete_artisan_timesheet_happy_path(patch_supabase):
    fake = patch_supabase({"select_return": [{"id": 3}]})
    result = await delete_artisan_timesheet(3, current_user=CURRENT_USER)
    assert result["success"] is True
    assert any(c["op"] == "delete" for c in fake.state["calls"])
