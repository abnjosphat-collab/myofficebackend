# tests/test_ppe_conflicts.py — the same kind of PPE is not issued to the same person on the same day twice; a stale edit
# is refused. (PPE had no duplicate protection at all: live data held nine groups of identical issues.)
import pytest
from fastapi import HTTPException

from app.ppe_conflicts import duplicate_message, find_duplicate
from app.routers import ppe as ppe_mod
from app.routers.ppe import PPEIssueCreate, PPEIssueUpdate, create_ppe_record, update_ppe_record


def rec(id, ppe_type="Safety boots", issue_date="2026-03-04", status="active", name="Reason Muzondo"):
    return {"id": id, "employee_name": name, "ppe_type": ppe_type, "item_name": "Boots", "issue_date": issue_date, "status": status}


def test_same_type_same_day_is_a_duplicate_whatever_the_case():
    assert find_duplicate([rec(1)], "safety BOOTS ", "2026-03-04")["id"] == 1


def test_other_type_other_day_returned_and_itself_are_not():
    assert find_duplicate([rec(1)], "Helmet", "2026-03-04") is None
    assert find_duplicate([rec(1)], "Safety boots", "2026-03-05") is None
    assert find_duplicate([rec(1, status="returned"), rec(2, status="lost")], "Safety boots", "2026-03-04") is None
    assert find_duplicate([rec(1)], "Safety boots", "2026-03-04", ignore_id=1) is None


def test_missing_values_cannot_be_compared():
    assert find_duplicate([rec(1)], "", "2026-03-04") is None and find_duplicate([rec(1)], "Safety boots", None) is None


def test_message_names_the_record():
    assert "#7" in duplicate_message(rec(7)) and "Reason Muzondo" in duplicate_message(rec(7))


class Resp:
    def __init__(self, data):
        self.data = data


class Q:
    def __init__(self, fake):
        self.fake, self.op, self.payload, self.filters = fake, "select", None, []

    def select(self, *a, **k):
        return self

    def eq(self, c, v):
        self.filters.append((c, v))
        return self

    def insert(self, d):
        self.op, self.payload = "insert", d
        return self

    def update(self, d):
        self.op, self.payload = "update", d
        return self

    def execute(self):
        self.fake.calls.append((self.op, self.payload, list(self.filters)))
        if self.op == "insert":
            return Resp([{"id": 99, **self.payload}])
        if self.op == "update":
            return Resp([{"id": 1, **self.payload}])
        if any(c == "id" for c, _ in self.filters):
            return Resp(self.fake.existing)
        return Resp(self.fake.others)


class Fake:
    def __init__(self, others=None, existing=None):
        self.others, self.existing, self.calls = others or [], existing or [], []

    def table(self, name):
        return Q(self)

    def writes(self, op):
        return [c for c in self.calls if c[0] == op]


@pytest.fixture
def fake(monkeypatch):
    def make(**kw):
        f = Fake(**kw)
        monkeypatch.setattr(ppe_mod, "supabase", f)
        return f
    return make


USER = {"user_id": "u1", "email": "e@x.com", "role": "user"}


def _issue(**kw):
    base = dict(employee_name="Reason Muzondo", employee_id="PM486", department="Engineering", position="Fitter", ppe_type="Safety boots",
                item_name="Boots", issue_date="2026-03-04")
    base.update(kw)
    return PPEIssueCreate(**base)


async def test_issuing_the_same_item_twice_in_a_day_is_refused(fake):
    f = fake(others=[rec(41)])
    with pytest.raises(HTTPException) as exc:
        await create_ppe_record(_issue(), current_user=USER)
    assert exc.value.status_code == 409 and "#41" in exc.value.detail and f.writes("insert") == []


async def test_a_different_item_the_same_day_is_saved(fake):
    f = fake(others=[rec(41, ppe_type="Helmet")])
    await create_ppe_record(_issue(), current_user=USER)
    assert len(f.writes("insert")) == 1


async def test_the_lookup_is_for_this_person_and_this_day(fake):
    f = fake()
    await create_ppe_record(_issue(), current_user=USER)
    lookup = f.writes("select")[0]
    assert ("employee_id", "PM486") in lookup[2] and ("issue_date", "2026-03-04") in lookup[2]


async def test_moving_a_record_onto_another_issue_is_refused(fake):
    existing = {"id": 5, "employee_id": "PM486", "ppe_type": "Helmet", "issue_date": "2026-03-04", "status": "active"}
    f = fake(others=[rec(41)], existing=[existing])
    with pytest.raises(HTTPException) as exc:
        await update_ppe_record(5, PPEIssueUpdate(ppe_type="Safety boots"), current_user=USER)
    assert exc.value.status_code == 409 and f.writes("update") == []


async def test_a_stale_edit_is_refused(fake):
    existing = {"id": 5, "employee_id": "PM486", "ppe_type": "Helmet", "issue_date": "2026-03-04", "status": "active", "updated_at": "2026-10-19T08:00:00+00:00"}
    f = fake(existing=[existing])
    with pytest.raises(HTTPException) as exc:
        await update_ppe_record(5, PPEIssueUpdate(notes="mine", expected_updated_at="2026-10-19T07:00:00+00:00"), current_user=USER)
    assert exc.value.status_code == 409 and "Someone else changed" in exc.value.detail and f.writes("update") == []


async def test_a_current_edit_is_saved_and_moves_updated_at(fake):
    existing = {"id": 5, "employee_id": "PM486", "ppe_type": "Helmet", "issue_date": "2026-03-04", "status": "active", "updated_at": "2026-10-19T08:00:00+00:00"}
    f = fake(existing=[existing])
    await update_ppe_record(5, PPEIssueUpdate(notes="mine", expected_updated_at="2026-10-19T08:00:00Z"), current_user=USER)
    payload = f.writes("update")[0][1]
    assert "expected_updated_at" not in payload and payload["updated_at"] > "2026-10-19T08:00:00+00:00"
