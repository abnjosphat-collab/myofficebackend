# tests/test_ppe_events.py — Mining PPE issue / return / correction events (batch 3).
#
# The register movement is one atomic server write: the event row is stored and
# the ppe_records projection is derived from it, never the reverse. Replaying
# an idempotency_key returns the stored result instead of writing twice; a
# wrong event is superseded by a correction, never edited or removed.
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.routers.ppe as ppe_mod
from app.routers.ppe import PPEEventIn, record_ppe_event


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, state, table_name):
        self.state = state
        self.table_name = table_name
        self._mode = None
        self._filters = {}
        self._payload = None
        self._range = None

    def select(self, *_a, **_k):
        self._mode = "select"
        return self

    def eq(self, field, value):
        self._filters[field] = value
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = dict(payload)
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = dict(payload)
        return self

    def _rows(self):
        if self.table_name == "ppe_events":
            return self.state["events"]
        if self.table_name == "ppe_records":
            return self.state["records"]
        if self.table_name == "ppe_matrix":
            return self.state["matrix"]
        return []

    def execute(self):
        if self._mode == "select":
            out = [r for r in self._rows()
                   if all(r.get(k) == v for k, v in self._filters.items())]
            if self._range is not None:
                out = out[self._range[0]:self._range[1] + 1]
            return _Result(out)
        if self._mode == "insert":
            if self.table_name == "ppe_events":
                if any(e.get("idempotency_key") == self._payload.get("idempotency_key")
                       for e in self.state["events"]):
                    raise Exception(
                        'duplicate key value violates unique constraint '
                        '"ppe_events_idempotency_key_key"')
                row = {"id": f"ev-{len(self.state['events']) + 1}", **self._payload}
                self.state["events"].append(row)
                return _Result([row])
            row = {"id": len(self.state["records"]) + 1, **self._payload}
            self.state["records"].append(row)
            return _Result([row])
        if self._mode == "update":
            for r in self._rows():
                if all(r.get(k) == v for k, v in self._filters.items()):
                    r.update(self._payload)
                    return _Result([r])
            return _Result([])
        raise AssertionError(f"unexpected mode {self._mode}")


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(self.state, name)


MINING_PERSON = {"employee_id": "C1001", "first_name": "Tendai", "last_name": "Moyo",
                 "designation": "Miner", "department": "Mining", "section": "Shaft A"}
ENG_PERSON = {"employee_id": "E2002", "first_name": "Rudo", "last_name": "Dube",
              "designation": "Fitter", "department": "Engineering", "section": "Plant"}


@pytest.fixture
def ppe_events_env(monkeypatch):
    state = {"records": [], "events": [], "matrix": [
        {"ppe_type": "jacket", "interval_months": 24, "department": "Mining"},
    ]}
    monkeypatch.setattr(ppe_mod, "supabase", _FakeSupabase(state))

    def _resolve(employee_id):
        eid = (employee_id or "").strip().upper()
        for person in (MINING_PERSON, ENG_PERSON):
            if person["employee_id"] == eid:
                return dict(person)
        raise HTTPException(status_code=422,
                            detail="That mine number is not on the employee register.")

    monkeypatch.setattr(ppe_mod, "_resolve_person_or_422", _resolve)
    return state


def _manager():
    return {"user_id": "u-1", "email": "m@x.com", "role": "manager"}


def _mining_viewer():
    return {"user_id": "u-9", "email": "mine@x.com", "role": "viewer",
            "scope": {"kind": "department", "value": "Mining"}}


def _issue(employee_id="C1001", key="k-1", **kw):
    from datetime import date
    args = {"kind": "issue", "employee_id": employee_id, "ppe_type": "jacket",
            "quantity": 1, "issue_date": date(2026, 2, 2), "idempotency_key": key}
    args.update(kw)
    return PPEEventIn(**args)


async def test_issue_creates_record_and_event_with_mining_expiry(ppe_events_env):
    result = await record_ppe_event(_issue(), _manager())
    assert result["duplicate"] is False
    assert result["status"] == "active"
    assert result["expiry_date"] == "2028-02-02"
    assert result["event_id"] == "ev-1"
    assert len(ppe_events_env["records"]) == 1
    assert len(ppe_events_env["events"]) == 1
    event = ppe_events_env["events"][0]
    assert event["kind"] == "issue"
    assert event["employee_id"] == "C1001"
    assert event["department"] == "Mining"
    assert event["idempotency_key"] == "k-1"
    assert event["actor"] == "m@x.com"
    assert event["effective_date"] == "2026-02-02"
    assert event["section"] == "Shaft A"


async def test_issue_replay_returns_stored_result_without_second_write(ppe_events_env):
    first = await record_ppe_event(_issue(), _manager())
    second = await record_ppe_event(_issue(), _manager())
    assert second["duplicate"] is True
    assert second["event_id"] == first["event_id"] == "ev-1"
    assert len(ppe_events_env["records"]) == 1
    assert len(ppe_events_env["events"]) == 1


async def test_issue_duplicate_same_day_conflicts(ppe_events_env):
    ppe_events_env["records"].append(
        {"id": 7, "employee_id": "C1001", "employee_name": "Tendai Moyo",
         "ppe_type": "jacket", "issue_date": "2026-02-02", "status": "active"})
    with pytest.raises(HTTPException) as exc:
        await record_ppe_event(_issue(key="k-2"), _manager())
    assert exc.value.status_code == 409


async def test_issue_needs_an_issue_date(ppe_events_env):
    with pytest.raises(HTTPException) as exc:
        await record_ppe_event(_issue(issue_date=None, key="k-3"), _manager())
    assert exc.value.status_code == 400


async def test_issue_unknown_mine_number_rejected(ppe_events_env):
    with pytest.raises(HTTPException) as exc:
        await record_ppe_event(_issue(employee_id="C9999", key="k-4"), _manager())
    assert exc.value.status_code == 422


async def test_scoped_viewer_cannot_issue_outside_scope(ppe_events_env):
    with pytest.raises(HTTPException) as exc:
        await record_ppe_event(_issue(employee_id="E2002", key="k-5"), _mining_viewer())
    assert exc.value.status_code == 404
    assert ppe_events_env["records"] == []
    assert ppe_events_env["events"] == []


async def test_return_marks_record_returned_and_rejects_second_return(ppe_events_env):
    issued = await record_ppe_event(_issue(), _manager())
    entry = PPEEventIn(kind="return", employee_id="C1001", ppe_type="jacket",
                       record_id=issued["record_id"], idempotency_key="k-ret")
    returned = await record_ppe_event(entry, _manager())
    assert returned["status"] == "returned"
    assert ppe_events_env["records"][0]["status"] == "returned"
    assert returned["section"] == "Shaft A"
    assert ppe_events_env["events"][-1]["section"] == "Shaft A"
    assert ppe_events_env["events"][-1]["effective_date"] != ""
    assert ppe_events_env["records"][0]["returned_date"] != ""
    with pytest.raises(HTTPException) as exc:
        await record_ppe_event(
            PPEEventIn(kind="return", employee_id="C1001", ppe_type="jacket",
                       record_id=issued["record_id"], idempotency_key="k-ret2"),
            _manager())
    assert exc.value.status_code == 409


async def test_return_needs_a_record(ppe_events_env):
    with pytest.raises(HTTPException) as exc:
        await record_ppe_event(
            PPEEventIn(kind="return", employee_id="C1001", ppe_type="jacket",
                       idempotency_key="k-ret3"),
            _manager())
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc2:
        await record_ppe_event(
            PPEEventIn(kind="return", employee_id="C1001", ppe_type="jacket",
                       record_id=4242, idempotency_key="k-ret4"),
            _manager())
    assert exc2.value.status_code == 404


async def test_correction_needs_supervisor_and_note(ppe_events_env):
    issued = await record_ppe_event(_issue(), _manager())
    with pytest.raises(HTTPException) as exc:
        await record_ppe_event(
            PPEEventIn(kind="correction", employee_id="C1001", ppe_type="jacket",
                       record_id=issued["record_id"], evidence={}, idempotency_key="k-cor1"),
            _manager())
    assert exc.value.status_code == 400


async def test_correction_supersedes_record_and_keeps_audit(ppe_events_env):
    from datetime import date
    issued = await record_ppe_event(_issue(), _manager())
    corrected = await record_ppe_event(
        PPEEventIn(kind="correction", employee_id="C1001", ppe_type="jacket",
                   record_id=issued["record_id"], issue_date=date(2026, 3, 3),
                   evidence={"supervisor": "S. Boss", "note": "wrong month captured"},
                   idempotency_key="k-cor2"),
        _manager())
    assert "issue_date" in corrected["updated"]
    record = ppe_events_env["records"][0]
    assert record["issue_date"] == "2026-03-03"
    event = ppe_events_env["events"][-1]
    assert corrected["effective_date"] == "2026-03-03"
    assert event["section"] == "Shaft A"
    assert event["evidence"]["supervisor"] == "S. Boss"
    assert event["evidence"]["before"]["issue_date"] == "2026-02-02"
    assert event["evidence"]["after"]["issue_date"] == "2026-03-03"


def test_zero_quantity_never_stored():
    from datetime import date
    with pytest.raises(ValidationError):
        PPEEventIn(kind="issue", employee_id="C1001", ppe_type="jacket", quantity=0,
                   issue_date=date(2026, 2, 2), idempotency_key="k-zero")
