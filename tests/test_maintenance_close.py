# tests/test_maintenance_close.py - M2: fast close, bulk close, and the
# not-done wiring through stats and overdue. Same recipe as
# test_maintenance_work_orders.py: call the route coroutine directly against a
# fake supabase client.
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.routers.maintenance as m
from app.routers.maintenance import (
    WorkOrderBulkClose, WorkOrderClose,
    bulk_close_work_orders, close_duration_text, close_problems, close_work_order,
    get_work_order_stats, hours_text,
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
        if self._mode == "update" and any(
                c == "id" and v in self.state.get("_fail_update_ids", ()) for c, v in self._filters):
            raise Exception("db exploded on write")
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

    def _patch(work_orders=None, columns=True, explode=(), fail_update_ids=()):
        state["work_orders"] = list(work_orders or [])
        state["_explode"] = tuple(explode)
        state["_fail_update_ids"] = tuple(fail_update_ids)
        monkeypatch.setattr(m, "supabase", _FakeSupabase(state))
        # Pin the M1 column probe (it caches YES process-wide; see M1 notes).
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
        "failure_mode": "", "artisan_name": "", "artisan_date": "",
        "time_work_started": "", "time_work_finished": "", "total_time_worked": "",
        "foreman_sign": "", "updated_at": "2026-10-26T08:00:00+00:00",
    }
    base.update(over)
    return base


def _updates(state):
    return [c["payload"] for c in state["calls"] if c["table"] == "work_orders" and c["mode"] == "update"]


# --- close_problems: the pure rule ---

def test_close_problems_empty_row_needs_what_and_time():
    assert close_problems(_row(), WorkOrderClose()) == [
        "Say what was done.",
        "Record the time worked: start and finish, or hours.",
    ]


def test_close_problems_counts_what_the_row_already_holds():
    row = _row(work_done_details="Seal replaced.", total_time_worked="2h 00m")
    assert close_problems(row, WorkOrderClose()) == []


def test_close_problems_times_need_both_ends_and_sense():
    row = _row(work_done_details="Done.")
    assert close_problems(row, WorkOrderClose(time_work_started="08:00")) == [
        "Give both the start and finish times, or hours instead."]
    assert close_problems(row, WorkOrderClose(time_work_started="25:00", time_work_finished="26:00")) == [
        "Times must look like 08:30."]
    assert close_problems(row, WorkOrderClose(time_work_started="08:00", time_work_finished="08:00")) == [
        "The finish time must be after the start time."]
    # A finish earlier than the start is the night shift, not an error.
    assert close_problems(row, WorkOrderClose(time_work_started="22:00", time_work_finished="02:00")) == []


def test_close_problems_hours_must_be_positive():
    row = _row(work_done_details="Done.")
    assert close_problems(row, WorkOrderClose(total_hours=0)) == ["Hours must be more than 0 (and below 1000)."]
    assert close_problems(row, WorkOrderClose(total_hours=-2)) == ["Hours must be more than 0 (and below 1000)."]
    assert close_problems(row, WorkOrderClose(total_hours=2.5)) == []


def test_close_problems_breakdown_needs_cause_and_mode():
    row = _row(work_done_details="Done.", total_time_worked="1h 00m", classification="breakdown")
    assert close_problems(row, WorkOrderClose()) == [
        "Say what caused the breakdown.", "Pick the failure mode."]
    assert close_problems(row, WorkOrderClose(cause_of_failure="Seal worn", failure_mode="Seal failure")) == []
    coded = _row(work_done_details="Done.", total_time_worked="1h 00m", classification="breakdown",
                 cause_of_failure="Seal worn", failure_mode="Seal failure")
    assert close_problems(coded, WorkOrderClose()) == []


def test_close_problems_rejects_a_bad_artisan_date():
    row = _row(work_done_details="Done.", total_time_worked="1h 00m")
    assert close_problems(row, WorkOrderClose(artisan_date="32 Oct")) == ["The date must be YYYY-MM-DD."]
    assert close_problems(row, WorkOrderClose(artisan_date="2026-10-26")) == []


def test_duration_formatting_matches_the_stored_shape():
    assert close_duration_text("08:00", "10:30") == "2h 30m"
    assert close_duration_text("22:00", "02:00") == "4h 00m"
    assert hours_text(2.5) == "2h 30m"
    assert hours_text(2) == "2h 00m"


# --- POST /work-orders/{id}/close ---

async def test_close_happy_path_times_mode(patch_supabase):
    state = patch_supabase([_row()])
    saved = await close_work_order(1, WorkOrderClose(
        work_done_details="Seal replaced.", time_work_started="08:00", time_work_finished="10:30",
        artisan_name="T. Banda"), _user())
    assert saved["status"] == "completed"
    assert saved["progress"] == 100
    assert saved["total_time_worked"] == "2h 30m"
    assert saved["artisan_name"] == "T. Banda"
    assert saved["artisan_date"] != ""  # defaulted to today (Harare)
    payload = _updates(state)[0]
    assert payload["completed_at"] and payload["started_at"]  # M1 stamps
    event = state["maintenance_events"][0]
    assert event["action"] == "closed"
    assert (event["from_status"], event["to_status"]) == ("pending", "completed")


async def test_close_hours_mode_writes_the_same_shape(patch_supabase):
    state = patch_supabase([_row()])
    saved = await close_work_order(1, WorkOrderClose(work_done_details="Checked.", total_hours=2.5), _user())
    assert saved["total_time_worked"] == "2h 30m"
    assert _updates(state)[0]["status"] == "completed"


async def test_close_defaults_the_artisan_to_the_session(patch_supabase):
    patch_supabase([_row()])
    saved = await close_work_order(
        1, WorkOrderClose(work_done_details="Done.", total_hours=1), _user(email="kuda@mine.test"))
    assert saved["artisan_name"] == "kuda@mine.test"


async def test_close_unknown_id_is_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await close_work_order(9, WorkOrderClose(work_done_details="x", total_hours=1), _user())
    assert exc.value.status_code == 404


async def test_close_refuses_terminal_states(patch_supabase):
    for status in ("completed", "cancelled", "not-done"):
        patch_supabase([_row(status=status)])
        with pytest.raises(HTTPException) as exc:
            await close_work_order(1, WorkOrderClose(work_done_details="x", total_hours=1), _user())
        assert exc.value.status_code == 409
        assert status in exc.value.detail


async def test_close_names_every_gap_at_once(patch_supabase):
    patch_supabase([_row(classification="breakdown")])
    with pytest.raises(HTTPException) as exc:
        await close_work_order(1, WorkOrderClose(), _user())
    assert exc.value.status_code == 422
    assert "what was done" in exc.value.detail and "breakdown" in exc.value.detail


async def test_close_stale_copy_is_409_and_writes_nothing(patch_supabase):
    state = patch_supabase([_row()])
    with pytest.raises(HTTPException) as exc:
        await close_work_order(1, WorkOrderClose(
            work_done_details="x", total_hours=1, expected_updated_at="2020-01-01T00:00:00+00:00"), _user())
    assert exc.value.status_code == 409
    assert _updates(state) == []


async def test_close_pre_sql_writes_no_stamps(patch_supabase):
    state = patch_supabase([_row()], columns=False)
    saved = await close_work_order(1, WorkOrderClose(work_done_details="x", total_hours=1), _user())
    assert saved["status"] == "completed"
    assert "completed_at" not in _updates(state)[0]


async def test_close_scoped_viewer_cannot_close_out_of_scope(patch_supabase):
    state = patch_supabase([_row(section="Mechanical")])
    with pytest.raises(HTTPException) as exc:
        await close_work_order(1, WorkOrderClose(work_done_details="x", total_hours=1),
                               _user(scope="Projects"))
    assert exc.value.status_code == 404
    assert _updates(state) == []


async def test_close_scoped_viewer_closes_own_section(patch_supabase):
    patch_supabase([_row(section="Projects")])
    saved = await close_work_order(1, WorkOrderClose(work_done_details="x", total_hours=1),
                                   _user(scope="Projects"))
    assert saved["status"] == "completed"


# --- POST /work-orders/bulk-close ---

def _bulk(**over):
    base = dict(ids=[1], work_done_details="Routine check done.", total_hours=0.5)
    base.update(over)
    return WorkOrderBulkClose(**base)


async def test_bulk_close_mixed_batch_reports_per_row(patch_supabase):
    patch_supabase([
        _row(id=1),
        _row(id=2, status="completed", work_done_details="d", total_time_worked="1h 00m"),
        _row(id=3, classification="breakdown"),
        _row(id=4, classification="project"),
    ])
    result = await bulk_close_work_orders(_bulk(ids=[1, 2, 3, 4, 99]), _user())
    assert result["closed"] == [1]
    by_id = {s["id"]: s["reason"] for s in result["skipped"]}
    assert by_id[2] == "Already completed."
    assert "breakdown" in by_id[3]
    assert "project" in by_id[4]
    assert by_id[99] == "It no longer exists."


async def test_bulk_close_writes_one_event_per_closed_row(patch_supabase):
    state = patch_supabase([_row(id=1), _row(id=2)])
    result = await bulk_close_work_orders(_bulk(ids=[1, 2]), _user())
    assert result == {"closed": [1, 2], "skipped": []}
    assert [e["action"] for e in state["maintenance_events"]] == ["closed", "closed"]


async def test_bulk_close_skips_rows_missing_the_shared_fields(patch_supabase):
    patch_supabase([_row(id=1)])
    result = await bulk_close_work_orders(WorkOrderBulkClose(ids=[1]), _user())
    assert result["closed"] == []
    assert "what was done" in result["skipped"][0]["reason"]


async def test_bulk_close_write_error_skips_the_row_and_keeps_the_rest(patch_supabase):
    state = patch_supabase([_row(id=1), _row(id=2)], fail_update_ids=(2,))
    result = await bulk_close_work_orders(_bulk(ids=[1, 2]), _user())
    assert result["closed"] == [1]
    assert result["skipped"] == [{"id": 2, "reason": "It could not be closed; try again."}]
    assert state["work_orders"][0]["status"] == "completed"
    assert state["work_orders"][1]["status"] == "pending"


def test_bulk_close_ids_are_capped():
    with pytest.raises(ValidationError):
        WorkOrderBulkClose(ids=[], work_done_details="x", total_hours=1)
    with pytest.raises(ValidationError):
        WorkOrderBulkClose(ids=list(range(101)), work_done_details="x", total_hours=1)


# --- stats: not-done is counted, terminal states are never overdue ---

async def test_stats_counts_not_done_and_excludes_terminal_from_overdue(patch_supabase):
    patch_supabase([
        {"status": "pending", "priority": "low", "due_date": "2020-01-01", "progress": 0},
        {"status": "not-done", "priority": "low", "due_date": "2020-01-01", "progress": 0},
        {"status": "cancelled", "priority": "low", "due_date": "2020-01-01", "progress": 0},
        {"status": "completed", "priority": "low", "due_date": "2020-01-01", "progress": 100},
    ])
    stats = await get_work_order_stats(current_user=_user())
    assert stats["not_done"] == 1
    assert stats["status_breakdown"]["not-done"] == 1
    assert stats["overdue_count"] == 1
