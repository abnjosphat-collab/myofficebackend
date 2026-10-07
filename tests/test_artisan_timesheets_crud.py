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
    def __init__(self, state, cfg, table):
        self.state = state
        self.cfg = cfg
        self.table = table
        self._op = "select"
        self._filters = []
        self._payload = None
        self._orders = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def gte(self, col, val):
        self._filters.append((col, val))
        return self

    def lte(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._orders.append((col, desc))
        return self

    def range(self, start, end):
        self._range = (start, end)
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
            {"table": self.table, "op": self._op, "filters": list(self._filters), "payload": self._payload, "range": getattr(self, "_range", None)}
        )
        if self.cfg.get("raise_op") == self._op and self.table == self.cfg.get("raise_table", "artisan_timesheets"):
            raise Exception(self.cfg.get("raise_msg", "boom"))
        if self._op == "insert":
            return _Resp(self.cfg.get("insert_return"))
        if self._op == "update":
            return _Resp(self.cfg.get("update_return"))
        if self._op == "delete":
            return _Resp(self.cfg.get("delete_return", [{"id": 1}]))
        if self.table == "leaves":
            return _Resp(self.cfg.get("leaves_return"))
        return _Resp(self.cfg.get("select_return"))


class _FakeSupabase:
    def __init__(self, cfg):
        self.state = {"calls": []}
        self.cfg = cfg

    def table(self, name):
        assert name in ("artisan_timesheets", "leaves")
        return _Query(self.state, self.cfg, name)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _apply(cfg: dict) -> _FakeSupabase:
        fake = _FakeSupabase(cfg)
        monkeypatch.setattr(mod, "supabase", fake)
        return fake
    return _apply


CURRENT_USER = {"user_id": "u1", "email": "u1@x.com", "role": "user"}


def _leave_row(**kw):
    row = {
        "employee_id": "C001",
        "leave_type": "annual",
        "start_date": "2024-03-05",
        "end_date": "2024-03-06",
        "status": "approved",
    }
    row.update(kw)
    return row


def _day(date="2024-03-01", **kw):
    row = {"date": date, "day": "Fri"}
    row.update(kw)
    return row


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
    result = await list_artisan_timesheets(employee_id="C001", year=2024, month=1, summary=False, current_user=CURRENT_USER)
    assert len(result) == 1
    assert isinstance(result[0]["daily_rows"], list)


async def test_create_artisan_timesheet_happy_path(patch_supabase):
    fake = patch_supabase({
        "select_return": None,
        "leaves_return": [],
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


async def test_list_summary_asks_for_no_daily_rows_and_returns_them_as_they_are(patch_supabase):
    fake = patch_supabase({"select_return": [{"id": 1, "employee_id": "C001", "employee_name": "Alice", "year": 2024, "month": 1, "updated_at": "2024-02-01"}]})
    result = await list_artisan_timesheets(employee_id=None, year=None, month=None, summary=True, current_user=CURRENT_USER)
    assert result == [{"id": 1, "employee_id": "C001", "employee_name": "Alice", "year": 2024, "month": 1, "updated_at": "2024-02-01"}]
    assert "daily_rows" not in mod.SUMMARY_COLUMNS
    assert "signature" not in mod.SUMMARY_COLUMNS
    assert fake.state["calls"][0]["range"] == (0, 999)  # read through the paged helper, not a single capped query


async def test_list_summary_failure_is_a_500_not_an_empty_list(patch_supabase):
    patch_supabase({"raise_op": "select", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await list_artisan_timesheets(employee_id=None, year=None, month=None, summary=True, current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_create_rejects_overtime_on_a_leave_status_day(patch_supabase):
    patch_supabase({"select_return": None, "leaves_return": []})
    body = ArtisanTimesheetCreate(
        employee_id="C001",
        employee_name="Alice",
        year=2024,
        month=3,
        daily_rows=[_day("2024-03-01", day_status="leave", normal_hrs=8, ot_15=2)],
    )
    with pytest.raises(HTTPException) as exc:
        await create_artisan_timesheet(body, current_user=CURRENT_USER)
    assert exc.value.status_code == 422
    assert "2024-03-01" in exc.value.detail


async def test_create_rejects_standby_and_signin_on_a_leave_day(patch_supabase):
    patch_supabase({"select_return": None, "leaves_return": []})
    body = ArtisanTimesheetCreate(
        employee_id="C001",
        employee_name="Alice",
        year=2024,
        month=3,
        daily_rows=[_day(
            "2024-03-02",
            day_status="sick",
            normal_hrs=8,
            on_standby=True,
            sign_in_time="07:00",
            sign_in_signature="data:image/png;base64,AAA",
        )],
    )
    with pytest.raises(HTTPException) as exc:
        await create_artisan_timesheet(body, current_user=CURRENT_USER)
    assert exc.value.status_code == 422
    assert "2024-03-02" in exc.value.detail


async def test_create_rejects_wrong_normal_hours_on_a_leave_day(patch_supabase):
    patch_supabase({"select_return": None, "leaves_return": []})
    body = ArtisanTimesheetCreate(
        employee_id="C001",
        employee_name="Alice",
        year=2024,
        month=3,
        daily_rows=[_day("2024-03-03", day_status="leave", normal_hrs=0)],
    )
    with pytest.raises(HTTPException) as exc:
        await create_artisan_timesheet(body, current_user=CURRENT_USER)
    assert exc.value.status_code == 422
    assert "2024-03-03" in exc.value.detail


async def test_create_rejects_work_on_an_approved_leave_date(patch_supabase):
    # The row was never marked — leave approved after the draft was filled —
    # but the date is approved leave, so overtime on it is still working on leave.
    patch_supabase({"select_return": None, "leaves_return": [_leave_row()]})
    body = ArtisanTimesheetCreate(
        employee_id="C001",
        employee_name="Alice",
        year=2024,
        month=3,
        daily_rows=[_day("2024-03-05", normal_hrs=0, ot_15=2)],
    )
    with pytest.raises(HTTPException) as exc:
        await create_artisan_timesheet(body, current_user=CURRENT_USER)
    assert exc.value.status_code == 422
    assert "2024-03-05" in exc.value.detail


async def test_create_matches_approved_leave_loosely(patch_supabase):
    patch_supabase({
        "select_return": None,
        "leaves_return": [_leave_row(employee_id=" c001 ", start_date="2024-03-05T00:00:00", end_date="2024-03-05T00:00:00")],
    })
    body = ArtisanTimesheetCreate(
        employee_id="C001",
        employee_name="Alice",
        year=2024,
        month=3,
        daily_rows=[_day("2024-03-05", normal_hrs=0, ot_20=1)],
    )
    with pytest.raises(HTTPException) as exc:
        await create_artisan_timesheet(body, current_user=CURRENT_USER)
    assert exc.value.status_code == 422


async def test_create_accepts_a_clean_leave_day_and_ignores_other_employees_leave(patch_supabase):
    patch_supabase({
        "select_return": None,
        "leaves_return": [_leave_row(employee_id="C999")],
        "insert_return": [{
            "id": 5,
            "employee_id": "C001",
            "employee_name": "Alice",
            "year": 2024,
            "month": 3,
            "daily_rows": "[]",
        }],
    })
    body = ArtisanTimesheetCreate(
        employee_id="C001",
        employee_name="Alice",
        year=2024,
        month=3,
        daily_rows=[
            _day("2024-03-01", day_status="leave", normal_hrs=8),
            _day("2024-03-05", normal_hrs=0, ot_15=2),
        ],
    )
    result = await create_artisan_timesheet(body, current_user=CURRENT_USER)
    assert result["id"] == 5


async def test_update_rejects_work_on_leave(patch_supabase):
    patch_supabase({
        "select_return": [{"id": 2, "employee_id": "C001", "employee_name": "Alice", "year": 2024, "month": 3, "daily_rows": "[]"}],
        "leaves_return": [],
        "update_return": [{"id": 2}],
    })
    with pytest.raises(HTTPException) as exc:
        await update_artisan_timesheet(
            2,
            ArtisanTimesheetUpdate(daily_rows=[_day("2024-03-04", day_status="leave", normal_hrs=8, ot_20=4)]),
            current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 422
    assert "2024-03-04" in exc.value.detail


async def test_update_without_daily_rows_skips_the_leave_check(patch_supabase):
    fake = patch_supabase({
        "select_return": [{"id": 2, "employee_id": "C001", "employee_name": "Alice", "year": 2024, "month": 3, "daily_rows": "[]"}],
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
        ArtisanTimesheetUpdate(compiled_by="Clerk"),
        current_user=CURRENT_USER,
    )
    assert result["adjustment"] == 1.5
    assert all(c["table"] == "artisan_timesheets" for c in fake.state["calls"])
