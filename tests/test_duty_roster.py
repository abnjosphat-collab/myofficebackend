from datetime import date

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers import duty_roster as mod
from app.routers.duty_roster import (
    DutyRosterCreate,
    DutyRosterUpdate,
    create_duty_entry,
    delete_duty_entry,
    get_duty_entry,
    list_duty_roster,
    update_duty_entry,
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

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
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
        assert name == "duty_roster"
        return _Query(self.state, self.cfg)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _apply(cfg: dict) -> _FakeSupabase:
        fake = _FakeSupabase(cfg)
        monkeypatch.setattr(mod, "supabase", fake)
        return fake
    return _apply


CURRENT_USER = {"user_id": "u1", "email": "u1@x.com", "role": "manager"}


def _entry(**kw):
    row = {
        "id": 1,
        "employee_id": "C001",
        "employee_name": "Alice",
        "phone": "+263771234567",
        "department": None,
        "date_from": "2026-10-06",
        "date_to": "2026-10-12",
        "note": None,
    }
    row.update(kw)
    return row


async def test_list_returns_rows_chronologically(patch_supabase):
    patch_supabase({"select_return": [_entry(id=2), _entry(id=1)]})
    result = await list_duty_roster(current_user=CURRENT_USER)
    assert [r["id"] for r in result] == [2, 1]


async def test_list_failure_is_a_500_not_an_empty_list(patch_supabase):
    patch_supabase({"raise_op": "select", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await list_duty_roster(current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_get_entry_happy_path_and_404(patch_supabase):
    patch_supabase({"select_return": [_entry()]})
    assert (await get_duty_entry(1, current_user=CURRENT_USER))["employee_name"] == "Alice"
    patch_supabase({"select_return": []})
    with pytest.raises(HTTPException) as exc:
        await get_duty_entry(9, current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_create_happy_path_stores_iso_dates_and_trims_department(patch_supabase):
    fake = patch_supabase({"select_return": [], "insert_return": [_entry(id=5)]})
    body = DutyRosterCreate(
        employee_id="C001",
        employee_name="Alice",
        department="  Engineering  ",
        date_from="2026-10-06",
        date_to="2026-10-12",
    )
    result = await create_duty_entry(body, current_user=CURRENT_USER)
    assert result["id"] == 5
    insert_call = next(c for c in fake.state["calls"] if c["op"] == "insert")
    assert insert_call["payload"]["date_from"] == "2026-10-06"
    assert insert_call["payload"]["date_to"] == "2026-10-12"
    assert insert_call["payload"]["department"] == "Engineering"


async def test_create_blank_department_becomes_mine_wide(patch_supabase):
    fake = patch_supabase({"select_return": [], "insert_return": [_entry(id=6)]})
    body = DutyRosterCreate(
        employee_id="C001", employee_name="Alice", department="   ",
        date_from="2026-10-06", date_to="2026-10-12",
    )
    await create_duty_entry(body, current_user=CURRENT_USER)
    insert_call = next(c for c in fake.state["calls"] if c["op"] == "insert")
    assert insert_call["payload"]["department"] is None


async def test_create_rejects_inverted_dates(patch_supabase):
    patch_supabase({})
    with pytest.raises(ValidationError):
        DutyRosterCreate(
            employee_id="C001", employee_name="Alice",
            date_from="2026-10-12", date_to="2026-10-06",
        )


async def test_create_rejects_overlap_in_the_same_scope(patch_supabase):
    patch_supabase({"select_return": [_entry(department="Engineering")]})
    body = DutyRosterCreate(
        employee_id="C002", employee_name="Bob", department="Engineering",
        date_from="2026-10-10", date_to="2026-10-16",
    )
    with pytest.raises(HTTPException) as exc:
        await create_duty_entry(body, current_user=CURRENT_USER)
    assert exc.value.status_code == 409
    assert "Alice" in exc.value.detail


async def test_create_matches_department_case_insensitively(patch_supabase):
    patch_supabase({"select_return": [_entry(department="Engineering")]})
    body = DutyRosterCreate(
        employee_id="C002", employee_name="Bob", department="engineering",
        date_from="2026-10-10", date_to="2026-10-16",
    )
    with pytest.raises(HTTPException) as exc:
        await create_duty_entry(body, current_user=CURRENT_USER)
    assert exc.value.status_code == 409


async def test_create_allows_touching_but_not_overlapping_ranges(patch_supabase):
    patch_supabase({"select_return": [_entry()], "insert_return": [_entry(id=7)]})
    body = DutyRosterCreate(
        employee_id="C002", employee_name="Bob",
        date_from="2026-10-13", date_to="2026-10-19",
    )
    result = await create_duty_entry(body, current_user=CURRENT_USER)
    assert result["id"] == 7


async def test_create_allows_mine_wide_alongside_department(patch_supabase):
    # Escalation chain, not a clash: different scopes may cover the same dates.
    patch_supabase({"select_return": [_entry(department="Engineering")], "insert_return": [_entry(id=8)]})
    body = DutyRosterCreate(
        employee_id="C002", employee_name="Bob",
        date_from="2026-10-10", date_to="2026-10-16",
    )
    result = await create_duty_entry(body, current_user=CURRENT_USER)
    assert result["id"] == 8


async def test_update_note_only_skips_overlap_and_stamps_updated_at(patch_supabase):
    fake = patch_supabase({
        "select_return": [_entry()],
        "update_return": [_entry(note="Weekend cover")],
    })
    result = await update_duty_entry(1, DutyRosterUpdate(note="Weekend cover"), current_user=CURRENT_USER)
    assert result["note"] == "Weekend cover"
    update_call = next(c for c in fake.state["calls"] if c["op"] == "update")
    assert update_call["payload"]["note"] == "Weekend cover"
    assert "updated_at" in update_call["payload"]


async def test_update_ignores_its_own_range(patch_supabase):
    patch_supabase({"select_return": [_entry()], "update_return": [_entry(note="x")]})
    # Same range, same scope: only itself overlaps, so the save goes through.
    result = await update_duty_entry(
        1,
        DutyRosterUpdate(date_from="2026-10-06", date_to="2026-10-12", note="x"),
        current_user=CURRENT_USER,
    )
    assert result["note"] == "x"


async def test_update_rejects_a_new_overlap(patch_supabase):
    clash = _entry(id=2, employee_id="C002", employee_name="Bob",
                   date_from="2026-10-13", date_to="2026-10-19")
    patch_supabase({"select_return": [_entry(), clash]})
    with pytest.raises(HTTPException) as exc:
        await update_duty_entry(
            1, DutyRosterUpdate(date_to="2026-10-15"), current_user=CURRENT_USER
        )
    assert exc.value.status_code == 409
    assert "Bob" in exc.value.detail


async def test_update_rejects_partially_inverted_dates(patch_supabase):
    patch_supabase({"select_return": [_entry()]})
    with pytest.raises(HTTPException) as exc:
        await update_duty_entry(
            1, DutyRosterUpdate(date_from="2026-10-20"), current_user=CURRENT_USER
        )
    assert exc.value.status_code == 422


async def test_update_without_fields_returns_existing(patch_supabase):
    fake = patch_supabase({"select_return": [_entry()]})
    result = await update_duty_entry(1, DutyRosterUpdate(), current_user=CURRENT_USER)
    assert result["id"] == 1
    assert all(c["op"] == "select" for c in fake.state["calls"])


async def test_delete_happy_path_and_404(patch_supabase):
    fake = patch_supabase({"select_return": [_entry()]})
    result = await delete_duty_entry(1, current_user=CURRENT_USER)
    assert result["success"] is True
    assert any(c["op"] == "delete" for c in fake.state["calls"])
    patch_supabase({"select_return": []})
    with pytest.raises(HTTPException) as exc:
        await delete_duty_entry(9, current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_update_model_rejects_both_dates_inverted(patch_supabase):
    patch_supabase({})
    with pytest.raises(ValidationError):
        DutyRosterUpdate(date_from="2026-10-12", date_to="2026-10-06")


async def test_overlap_tolerates_date_objects_from_the_db(patch_supabase):
    patch_supabase({"select_return": [_entry(date_from=date(2026, 10, 6), date_to=date(2026, 10, 12))]})
    body = DutyRosterCreate(
        employee_id="C002", employee_name="Bob",
        date_from="2026-10-10", date_to="2026-10-16",
    )
    with pytest.raises(HTTPException) as exc:
        await create_duty_entry(body, current_user=CURRENT_USER)
    assert exc.value.status_code == 409


async def test_get_failure_is_a_500(patch_supabase):
    patch_supabase({"raise_op": "select", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await get_duty_entry(1, current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_create_empty_insert_is_a_500(patch_supabase):
    patch_supabase({"select_return": [], "insert_return": None})
    body = DutyRosterCreate(
        employee_id="C001", employee_name="Alice",
        date_from="2026-10-06", date_to="2026-10-12",
    )
    with pytest.raises(HTTPException) as exc:
        await create_duty_entry(body, current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_create_db_failure_is_a_500(patch_supabase):
    patch_supabase({"select_return": [], "raise_op": "insert", "raise_msg": "db down"})
    body = DutyRosterCreate(
        employee_id="C001", employee_name="Alice",
        date_from="2026-10-06", date_to="2026-10-12",
    )
    with pytest.raises(HTTPException) as exc:
        await create_duty_entry(body, current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_update_department_is_trimmed(patch_supabase):
    fake = patch_supabase({
        "select_return": [_entry()],
        "update_return": [_entry(department="Engineering")],
    })
    await update_duty_entry(1, DutyRosterUpdate(department="  Engineering "), current_user=CURRENT_USER)
    update_call = next(c for c in fake.state["calls"] if c["op"] == "update")
    assert update_call["payload"]["department"] == "Engineering"


async def test_update_empty_result_is_a_500(patch_supabase):
    patch_supabase({"select_return": [_entry()], "update_return": None})
    with pytest.raises(HTTPException) as exc:
        await update_duty_entry(1, DutyRosterUpdate(note="x"), current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_update_db_failure_is_a_500(patch_supabase):
    patch_supabase({"select_return": [_entry()], "raise_op": "update", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await update_duty_entry(1, DutyRosterUpdate(note="x"), current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_delete_db_failure_is_a_500(patch_supabase):
    patch_supabase({"select_return": [_entry()], "raise_op": "delete", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await delete_duty_entry(1, current_user=CURRENT_USER)
    assert exc.value.status_code == 500
