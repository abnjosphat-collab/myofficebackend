# tests/test_leave_conflicts.py — one person cannot have two live leave requests that share a day, who approved a leave
# is the signed-in person, an approved leave is not deleted, and a stale edit is refused.
import pytest
from fastapi import HTTPException

from app import audit
from app.leave_conflicts import conflict_message, find_conflict
from app.routers import leaves as leaves_mod
from app.routers.leaves import LeaveCreate, LeaveUpdate, BulkLeaveStatusUpdate, create_leave, update_leave, delete_leave, bulk_update_leave_status


def row(id, start="2026-03-10", end="2026-03-12", status="pending", name="Reason Muzondo", kind="annual"):
    return {"id": id, "employee_name": name, "start_date": start, "end_date": end, "leave_type": kind, "status": status}


# ─── the rule ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_identical_and_overlapping_ranges_conflict():
    assert find_conflict([row(1)], "2026-03-10", "2026-03-12")["id"] == 1
    assert find_conflict([row(1)], "2026-03-12", "2026-03-15")["id"] == 1  # share the 12th
    assert find_conflict([row(1)], "2026-03-01", "2026-03-31")["id"] == 1  # swallows it
    assert find_conflict([row(1)], "2026-03-11", "2026-03-11")["id"] == 1  # inside it


def test_adjacent_ranges_do_not_conflict():
    assert find_conflict([row(1)], "2026-03-13", "2026-03-14") is None
    assert find_conflict([row(1)], "2026-03-08", "2026-03-09") is None


def test_itself_rejected_and_cancelled_are_ignored():
    assert find_conflict([row(1)], "2026-03-10", "2026-03-12", ignore_id=1) is None
    assert find_conflict([row(1, status="rejected"), row(2, status="cancelled")], "2026-03-10", "2026-03-12") is None
    assert find_conflict([row(3, status="approved")], "2026-03-10", "2026-03-12")["id"] == 3


def test_bad_input_never_raises():
    assert find_conflict([row(1)], "nope", "2026-03-12") is None
    assert find_conflict([row(1)], "2026-03-12", "2026-03-10") is None  # inverted: validation rejects it elsewhere
    assert find_conflict([{"id": 9, "start_date": "x", "end_date": None}], "2026-03-10", "2026-03-12") is None


def test_message_names_the_leave_in_the_way():
    text = conflict_message(row(41, kind="sick", status="approved"))
    assert "Reason Muzondo" in text and "2026-03-10" in text and "#41" in text and "approved" in text and "sick" in text


# ─── the handlers ──────────────────────────────────────────────────────────────────────────────────────────────────

class Resp:
    def __init__(self, data):
        self.data = data


class Q:
    def __init__(self, fake):
        self.fake, self.op, self.payload, self.filters = fake, "select", None, []

    def select(self, *a, **k):
        self.cols = a[0] if a else "*"
        return self

    def eq(self, c, v):
        self.filters.append((c, v))
        return self

    def in_(self, c, v):
        self.filters.append((c, list(v)))
        return self

    def lte(self, c, v):
        self.filters.append((c, "lte", v))
        return self

    def gte(self, c, v):
        self.filters.append((c, "gte", v))
        return self

    def limit(self, n):
        return self

    def insert(self, d):
        self.op, self.payload = "insert", d
        return self

    def update(self, d):
        self.op, self.payload = "update", d
        return self

    def delete(self):
        self.op = "delete"
        return self

    def execute(self):
        self.fake.calls.append((self.op, self.payload, list(self.filters)))
        if self.op == "insert":
            return Resp([{"id": 99, **self.payload}])
        if self.op == "update":
            return Resp([{"id": 1, **self.payload}])
        if self.op == "delete":
            return Resp([{"id": 1}])
        if any(c == "id" for c, *_ in self.filters):
            return Resp(self.fake.existing)
        if hasattr(self, "cols") and "approved_by" in str(self.cols):  # the has_columns probe
            if self.fake.has_decision_columns:
                return Resp([])
            raise Exception("column leaves.approved_by does not exist (42703)")
        return Resp(self.fake.others)


class Fake:
    def __init__(self, others=None, existing=None, has_decision_columns=True):
        self.others, self.existing, self.has_decision_columns, self.calls = others or [], existing or [], has_decision_columns, []

    def table(self, name):
        assert name == "leaves"
        return Q(self)

    def updates(self):
        return [c for c in self.calls if c[0] == "update"]


@pytest.fixture
def fake(monkeypatch):
    def make(**kw):
        f = Fake(**kw)
        monkeypatch.setattr(leaves_mod, "supabase", f)
        audit._COLUMN_PROBE.clear()
        return f
    yield make
    audit._COLUMN_PROBE.clear()


USER = {"user_id": "u1", "email": "e@x.com", "role": "manager", "name": "Edson Mavhondo"}


def _leave(**kw):
    base = dict(employee_id="PM486", employee_name="Reason Muzondo", position="Fitter", contact_number="0770", leave_type="annual",
                start_date="2026-03-10", end_date="2026-03-12", reason="Rest")
    base.update(kw)
    return LeaveCreate(**base)


async def test_creating_an_overlapping_leave_is_refused(fake):
    f = fake(others=[row(41, start="2026-03-11", end="2026-03-14", status="approved")])
    with pytest.raises(HTTPException) as exc:
        await create_leave(_leave(), current_user=USER)
    assert exc.value.status_code == 409 and "#41" in exc.value.detail
    assert [c for c in f.calls if c[0] == "insert"] == []


async def test_creating_a_leave_on_other_days_is_saved(fake):
    f = fake(others=[row(41, start="2026-04-01", end="2026-04-05")])
    await create_leave(_leave(), current_user=USER)
    assert len([c for c in f.calls if c[0] == "insert"]) == 1


async def test_the_overlap_lookup_is_for_this_person_and_these_dates(fake):
    f = fake()
    await create_leave(_leave(), current_user=USER)
    lookup = next(c for c in f.calls if c[0] == "select")
    assert ("employee_id", "PM486") in lookup[2] and ("start_date", "lte", "2026-03-12") in lookup[2] and ("end_date", "gte", "2026-03-10") in lookup[2]


async def test_moving_the_dates_onto_another_leave_is_refused(fake):
    existing = {"id": 5, "employee_id": "PM486", "start_date": "2026-05-01", "end_date": "2026-05-03", "status": "pending", "leave_type": "annual"}
    f = fake(others=[row(41, start="2026-03-10", end="2026-03-12")], existing=[existing])
    with pytest.raises(HTTPException) as exc:
        await update_leave(5, LeaveUpdate(start_date="2026-03-11", end_date="2026-03-11"), authorization=None, current_user=USER)
    assert exc.value.status_code == 409 and f.updates() == []


async def test_reopening_a_rejected_leave_that_now_overlaps_is_refused(fake, monkeypatch):
    existing = {"id": 5, "employee_id": "PM486", "start_date": "2026-03-10", "end_date": "2026-03-12", "status": "rejected", "leave_type": "annual"}
    f = fake(others=[row(41, start="2026-03-10", end="2026-03-12", status="approved")], existing=[existing])

    async def allow(authorization):
        return USER

    monkeypatch.setattr(leaves_mod, "require_role", lambda role: allow)
    monkeypatch.setattr(leaves_mod, "require_role_if_status_in", lambda *a, **k: allow(None))
    with pytest.raises(HTTPException) as exc:
        await update_leave(5, LeaveUpdate(status="pending"), authorization="Bearer t", current_user=USER)
    assert exc.value.status_code == 409 and f.updates() == []


async def test_approval_is_stamped_from_the_session_when_the_columns_exist(fake, monkeypatch):
    existing = {"id": 5, "employee_id": "PM486", "start_date": "2026-03-10", "end_date": "2026-03-12", "status": "pending", "updated_at": "2026-10-19T08:00:00+00:00"}
    f = fake(existing=[existing])

    async def allow(authorization):
        return USER

    monkeypatch.setattr(leaves_mod, "require_role", lambda role: allow)
    monkeypatch.setattr(leaves_mod, "require_role_if_status_in", lambda *a, **k: allow(None))
    await update_leave(5, LeaveUpdate(status="approved"), authorization="Bearer t", current_user=USER)
    payload = f.updates()[0][1]
    assert payload["approved_by"] == "Edson Mavhondo" and payload["approved_at"] and payload["updated_at"] > "2026-10-19T08:00:00+00:00"


async def test_approval_still_works_before_the_columns_exist(fake, monkeypatch):
    existing = {"id": 5, "employee_id": "PM486", "start_date": "2026-03-10", "end_date": "2026-03-12", "status": "pending"}
    f = fake(existing=[existing], has_decision_columns=False)

    async def allow(authorization):
        return USER

    monkeypatch.setattr(leaves_mod, "require_role", lambda role: allow)
    monkeypatch.setattr(leaves_mod, "require_role_if_status_in", lambda *a, **k: allow(None))
    await update_leave(5, LeaveUpdate(status="approved"), authorization="Bearer t", current_user=USER)
    payload = f.updates()[0][1]
    assert payload == {"status": "approved"} or set(payload) <= {"status", "updated_at"}  # no approver columns written


async def test_bulk_approval_is_stamped_from_the_session(fake, monkeypatch):
    f = fake()

    async def allow(authorization):
        return USER

    monkeypatch.setattr(leaves_mod, "require_role_if_status_in", lambda *a, **k: allow(None))
    await bulk_update_leave_status(BulkLeaveStatusUpdate(ids=[1, 2], status="approved"), authorization="Bearer t", current_user=USER)
    assert f.updates()[0][1]["approved_by"] == "Edson Mavhondo"


async def test_a_stale_edit_is_refused(fake, monkeypatch):
    existing = {"id": 5, "employee_id": "PM486", "start_date": "2026-03-10", "end_date": "2026-03-12", "status": "pending", "updated_at": "2026-10-19T08:00:00+00:00"}
    f = fake(existing=[existing])
    with pytest.raises(HTTPException) as exc:
        await update_leave(5, LeaveUpdate(reason="mine", expected_updated_at="2026-10-19T07:00:00+00:00"), authorization=None, current_user=USER)
    assert exc.value.status_code == 409 and "Someone else changed" in exc.value.detail and f.updates() == []


async def test_an_approved_leave_cannot_be_deleted(fake):
    f = fake(existing=[{"id": 5, "status": "approved"}])
    with pytest.raises(HTTPException) as exc:
        await delete_leave(5, current_user=USER)
    assert exc.value.status_code == 409 and "Reject it instead" in exc.value.detail
    assert [c for c in f.calls if c[0] == "delete"] == []


async def test_a_pending_leave_can_be_deleted(fake):
    f = fake(existing=[{"id": 5, "status": "pending"}])
    await delete_leave(5, current_user=USER)
    assert len([c for c in f.calls if c[0] == "delete"]) == 1


async def test_changing_only_one_date_cannot_invert_the_range(fake):
    existing = {"id": 5, "employee_id": "PM486", "start_date": "2026-03-10", "end_date": "2026-03-12", "status": "pending"}
    f = fake(existing=[existing])
    with pytest.raises(HTTPException) as exc:
        await update_leave(5, LeaveUpdate(end_date="2026-03-01"), authorization=None, current_user=USER)
    assert exc.value.status_code == 422 and f.updates() == []
