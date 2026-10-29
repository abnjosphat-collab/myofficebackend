# tests/test_overtime_conflicts.py — one person cannot have two live overtime requests that cover the same time.
# The register only warned in the browser (from whatever list that laptop had), so a second laptop recorded the same
# person, date and time again and both were approved. The rule now lives on the server; these tests pin it.
import pytest
from fastapi import HTTPException

from app.overtime_conflicts import conflict_message, find_conflict, neighbour_dates, time_window
from app.routers import overtime as overtime_mod
from app.routers.overtime import OvertimeCreate, OvertimeUpdate, create_overtime, update_overtime, BulkEditUpdate, bulk_edit_overtime


def row(id, date="2026-03-04", start="04:00", end="06:00", status="pending", name="Reason Muzondo"):
    return {"id": id, "employee_name": name, "date": date, "start_time": start, "end_time": end, "status": status}


# ─── the rule itself ───────────────────────────────────────────────────────────────────────────────────────────────

def test_the_same_window_is_a_conflict():
    assert find_conflict([row(1)], "2026-03-04", "04:00", "06:00")["id"] == 1


def test_a_partial_overlap_is_a_conflict():
    assert find_conflict([row(1, start="17:00", end="19:00")], "2026-03-04", "18:00", "20:00")["id"] == 1
    assert find_conflict([row(1, start="17:00", end="20:00")], "2026-03-04", "18:00", "19:00")["id"] == 1


def test_back_to_back_windows_are_allowed():
    assert find_conflict([row(1, start="17:00", end="19:00")], "2026-03-04", "19:00", "21:00") is None
    assert find_conflict([row(1, start="19:00", end="21:00")], "2026-03-04", "17:00", "19:00") is None


def test_separate_shifts_on_the_same_day_are_allowed():
    assert find_conflict([row(1, start="07:00", end="17:00")], "2026-03-04", "20:00", "23:00") is None


def test_a_request_does_not_conflict_with_itself():
    assert find_conflict([row(7)], "2026-03-04", "04:00", "06:00", ignore_id=7) is None
    assert find_conflict([row(7)], "2026-03-04", "04:00", "06:00", ignore_id="7") is None


def test_rejected_and_cancelled_requests_do_not_claim_the_time():
    assert find_conflict([row(1, status="rejected"), row(2, status="cancelled")], "2026-03-04", "04:00", "06:00") is None
    assert find_conflict([row(3, status="approved")], "2026-03-04", "04:00", "06:00")["id"] == 3
    assert find_conflict([row(4, status="paid")], "2026-03-04", "04:00", "06:00")["id"] == 4


def test_a_night_shift_runs_past_midnight():
    assert time_window("2026-03-04", "22:00", "06:00") == (time_window("2026-03-04", "22:00", "23:00")[0], time_window("2026-03-05", "06:00", "07:00")[0])
    # 22:00-06:00 on the 4th covers 02:00-04:00 on the 5th
    assert find_conflict([row(1, date="2026-03-04", start="22:00", end="06:00")], "2026-03-05", "02:00", "04:00")["id"] == 1
    # ...but not 06:00 onwards
    assert find_conflict([row(1, date="2026-03-04", start="22:00", end="06:00")], "2026-03-05", "06:00", "08:00") is None


def test_requests_without_times_cannot_be_compared():
    assert find_conflict([row(1)], "2026-03-04", None, None) is None
    assert find_conflict([row(1, start=None, end=None)], "2026-03-04", "04:00", "06:00") is None


def test_bad_values_never_raise():
    assert find_conflict([row(1)], "not-a-date", "04:00", "06:00") is None
    assert find_conflict([row(1)], "2026-03-04", "25:99", "06:00") is None
    assert neighbour_dates("nonsense") == []


def test_neighbour_dates_are_the_day_before_the_day_and_the_day_after():
    assert neighbour_dates("2026-03-01") == ["2026-02-28", "2026-03-01", "2026-03-02"]


def test_the_message_names_the_request_in_the_way():
    text = conflict_message(row(211, start="02:00", end="07:00", status="approved"))
    assert "Reason Muzondo" in text and "2026-03-04" in text and "02:00" in text and "#211" in text and "approved" in text


# ─── the handlers ──────────────────────────────────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, fake):
        self.fake = fake
        self.op = "select"
        self.payload = None
        self.filters = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def in_(self, col, vals):
        self.filters.append((col, tuple(vals)))
        return self

    def insert(self, data):
        self.op, self.payload = "insert", data
        return self

    def update(self, data):
        self.op, self.payload = "update", data
        return self

    def execute(self):
        self.fake.calls.append((self.op, self.payload, list(self.filters)))
        if self.op == "insert":
            return _Resp([{"id": 999, **self.payload, "spares_used": "[]"}])
        if self.op == "update":
            return _Resp([{"id": 1, **self.payload, "spares_used": "[]"}])
        # a select: either the clash lookup (has an employee_id/date filter) or get_or_404 (has an id filter)
        if any(col == "id" for col, _ in self.filters):
            return _Resp(self.fake.existing)
        return _Resp(self.fake.others)


class _Fake:
    def __init__(self, others, existing=None):
        self.others = others
        self.existing = existing or []
        self.calls = []

    def table(self, name):
        assert name == "overtime"
        return _Query(self)

    def inserted(self):
        return [c for c in self.calls if c[0] == "insert"]


@pytest.fixture
def fake(monkeypatch):
    def _make(others, existing=None):
        f = _Fake(others, existing)
        monkeypatch.setattr(overtime_mod, "supabase", f)
        return f
    return _make


USER = {"user_id": "u1", "email": "a@b.c", "role": "user"}


def _create(**kw):
    base = dict(employee_name="Reason Muzondo", employee_id="PM486", position="Fitter", overtime_type="regular", date="2026-03-04", start_time="02:00", end_time="07:00")
    base.update(kw)
    return OvertimeCreate(**base)


async def test_creating_the_same_overtime_again_is_refused_with_409(fake):
    f = fake([row(211, start="02:00", end="07:00", status="approved")])
    with pytest.raises(HTTPException) as exc:
        await create_overtime(_create(), current_user=USER)
    assert exc.value.status_code == 409
    assert "already has overtime" in exc.value.detail and "#211" in exc.value.detail
    assert f.inserted() == []


async def test_the_clash_lookup_is_scoped_to_the_person_and_the_nearby_dates(fake):
    f = fake([])
    await create_overtime(_create(), current_user=USER)
    lookup = next(c for c in f.calls if c[0] == "select")
    assert ("employee_id", "PM486") in lookup[2]
    assert ("date", ("2026-03-03", "2026-03-04", "2026-03-05")) in lookup[2]


async def test_a_different_time_for_the_same_person_is_saved(fake):
    f = fake([row(211, start="02:00", end="07:00", status="approved")])
    await create_overtime(_create(start_time="17:00", end_time="20:00"), current_user=USER)
    assert len(f.inserted()) == 1


async def test_hours_only_requests_are_not_blocked(fake):
    f = fake([row(211, start="02:00", end="07:00")])
    await create_overtime(_create(start_time=None, end_time=None, hours=3.0), current_user=USER)
    assert len(f.inserted()) == 1


async def test_moving_a_request_onto_another_one_is_refused(fake):
    existing = {"id": 5, "employee_id": "PM486", "employee_name": "Reason Muzondo", "date": "2026-03-04", "start_time": "17:00", "end_time": "20:00", "status": "pending"}
    f = fake([row(211, start="02:00", end="07:00")], existing=[existing])
    with pytest.raises(HTTPException) as exc:
        await update_overtime(5, OvertimeUpdate(start_time="02:00", end_time="07:00"), authorization=None, current_user=USER)
    assert exc.value.status_code == 409
    assert [c for c in f.calls if c[0] == "update"] == []


async def test_editing_something_else_on_a_request_is_not_blocked_by_its_own_twin(fake):
    existing = {"id": 5, "employee_id": "PM486", "employee_name": "Reason Muzondo", "date": "2026-03-04", "start_time": "02:00", "end_time": "07:00", "status": "pending"}
    f = fake([row(211, start="02:00", end="07:00")], existing=[existing])
    await update_overtime(5, OvertimeUpdate(reason="Breakdown call-out"), authorization=None, current_user=USER)
    assert len([c for c in f.calls if c[0] == "update"]) == 1


async def test_bulk_moving_to_a_date_skips_requests_that_would_overlap(fake):
    mover = {"id": 5, "employee_id": "PM486", "employee_name": "Reason Muzondo", "date": "2026-03-10", "start_time": "02:00", "end_time": "07:00", "status": "pending"}
    f = fake([row(211, date="2026-03-04", start="02:00", end="07:00", status="approved")], existing=[mover])
    result = await bulk_edit_overtime(BulkEditUpdate(ids=[5], date="2026-03-04"), authorization=None, current_user=USER)
    assert result.succeeded == 0 and result.failed == 1
    assert any("was not moved" in e for e in result.errors)
    assert [c for c in f.calls if c[0] == "update"] == []


async def test_bulk_new_times_skip_requests_that_would_overlap_on_their_own_date(fake):
    # Same person, same day, already approved 18:00-20:00: moving the pending 05:00-07:00 request to 17:00-19:00 clashes.
    mover = {"id": 5, "employee_id": "PM486", "employee_name": "Reason Muzondo", "date": "2026-03-04", "start_time": "05:00", "end_time": "07:00", "status": "pending"}
    f = fake([row(211, start="18:00", end="20:00", status="approved")], existing=[mover])
    result = await bulk_edit_overtime(BulkEditUpdate(ids=[5], start_time="17:00", end_time="19:00"), authorization=None, current_user=USER)
    assert result.succeeded == 0 and result.failed == 1
    assert any("was not moved" in e for e in result.errors)
    assert [c for c in f.calls if c[0] == "update"] == []


async def test_bulk_new_times_that_fit_are_saved_and_clear_typed_hours(fake):
    mover = {"id": 1, "employee_id": "PM486", "employee_name": "Reason Muzondo", "date": "2026-03-04", "start_time": None, "end_time": None, "status": "pending"}
    f = fake([row(211, start="07:00", end="16:00", status="approved")], existing=[mover])
    result = await bulk_edit_overtime(BulkEditUpdate(ids=[1], start_time="17:00", end_time="20:00"), authorization=None, current_user=USER)
    assert result.succeeded == 1 and result.errors == []
    update = next(c for c in f.calls if c[0] == "update")
    assert update[1] == {"start_time": "17:00", "end_time": "20:00", "hours": None}


async def test_bulk_new_date_and_times_are_checked_together(fake):
    # Neither the new date alone (old times 05:00-07:00) nor the new times alone (old date) clash; together they do.
    mover = {"id": 5, "employee_id": "PM486", "employee_name": "Reason Muzondo", "date": "2026-03-10", "start_time": "05:00", "end_time": "07:00", "status": "pending"}
    f = fake([row(211, date="2026-03-04", start="18:00", end="20:00", status="approved")], existing=[mover])
    result = await bulk_edit_overtime(BulkEditUpdate(ids=[5], date="2026-03-04", start_time="19:00", end_time="21:00"), authorization=None, current_user=USER)
    assert result.succeeded == 0 and any("was not moved" in e for e in result.errors)
    assert [c for c in f.calls if c[0] == "update"] == []
