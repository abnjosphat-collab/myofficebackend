from datetime import date

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers import rotation_covers as mod
from app.routers.rotation_covers import (
    RotationCoverCreate,
    RotationCoverUpdate,
    create_cover,
    delete_cover,
    get_cover,
    list_covers,
    update_cover,
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
        assert name == "rotation_covers"
        return _Query(self.state, self.cfg)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _apply(cfg: dict) -> _FakeSupabase:
        fake = _FakeSupabase(cfg)
        monkeypatch.setattr(mod, "supabase", fake)
        return fake
    return _apply


CURRENT_USER = {"user_id": "u1", "email": "u1@x.com", "role": "manager"}


def _cover(**kw):
    row = {
        "id": 1,
        "kind": "standby",
        "rotation_id": 3,
        "absent_employee_id": "C001",
        "absent_employee_name": "Alice",
        "cover_employee_id": "C002",
        "cover_employee_name": "Bob",
        "cover_phone": None,
        "date_from": "2026-10-06",
        "date_to": "2026-10-12",
        "reason": "leave",
    }
    row.update(kw)
    return row


def _body(**kw):
    body = {
        "kind": "standby",
        "rotation_id": 3,
        "absent_employee_id": "C001",
        "absent_employee_name": "Alice",
        "cover_employee_id": "C002",
        "cover_employee_name": "Bob",
        "date_from": "2026-10-06",
        "date_to": "2026-10-12",
    }
    body.update(kw)
    return RotationCoverCreate(**body)


async def test_list_returns_rows_chronologically(patch_supabase):
    patch_supabase({"select_return": [_cover(id=2), _cover(id=1)]})
    result = await list_covers(kind=None, rotation_id=None, current_user=CURRENT_USER)
    assert [r["id"] for r in result] == [2, 1]


async def test_list_filters_by_kind_and_rotation(patch_supabase):
    fake = patch_supabase({"select_return": []})
    await list_covers(kind="duty", rotation_id=7, current_user=CURRENT_USER)
    filters = [c["filters"] for c in fake.state["calls"] if c["op"] == "select"][0]
    assert ("kind", "duty") in filters
    assert ("rotation_id", 7) in filters


async def test_list_failure_is_a_500_not_an_empty_list(patch_supabase):
    patch_supabase({"raise_op": "select", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await list_covers(kind=None, rotation_id=None, current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_get_cover_happy_path_and_404(patch_supabase):
    patch_supabase({"select_return": [_cover()]})
    assert (await get_cover(1, current_user=CURRENT_USER))["cover_employee_name"] == "Bob"
    patch_supabase({"select_return": []})
    with pytest.raises(HTTPException) as exc:
        await get_cover(9, current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_create_happy_path_stores_iso_dates(patch_supabase):
    fake = patch_supabase({"select_return": [], "insert_return": [_cover(id=5)]})
    result = await create_cover(_body(), current_user=CURRENT_USER)
    assert result["id"] == 5
    insert_call = next(c for c in fake.state["calls"] if c["op"] == "insert")
    assert insert_call["payload"]["date_from"] == "2026-10-06"
    assert insert_call["payload"]["date_to"] == "2026-10-12"
    assert insert_call["payload"]["kind"] == "standby"


async def test_create_normalises_kind_case(patch_supabase):
    fake = patch_supabase({"select_return": [], "insert_return": [_cover(id=6, kind="duty")]})
    await create_cover(_body(kind="Duty"), current_user=CURRENT_USER)
    insert_call = next(c for c in fake.state["calls"] if c["op"] == "insert")
    assert insert_call["payload"]["kind"] == "duty"


async def test_create_rejects_unknown_kind_and_inverted_dates(patch_supabase):
    patch_supabase({})
    with pytest.raises(ValidationError):
        _body(kind="overtime")
    with pytest.raises(ValidationError):
        _body(date_from="2026-10-12", date_to="2026-10-06")


async def test_create_rejects_covering_yourself(patch_supabase):
    patch_supabase({})
    with pytest.raises(HTTPException) as exc:
        await create_cover(
            _body(cover_employee_id=" c001 ", cover_employee_name="Alice"),
            current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 422
    assert "in place of themselves" in exc.value.detail


async def test_create_rejects_a_second_cover_for_the_same_person(patch_supabase):
    patch_supabase({"select_return": [_cover()]})
    with pytest.raises(HTTPException) as exc:
        await create_cover(
            _body(cover_employee_id="C009", cover_employee_name="Zed",
                  date_from="2026-10-10", date_to="2026-10-16"),
            current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 409
    assert "Bob" in exc.value.detail


async def test_create_matches_absent_person_case_insensitively(patch_supabase):
    patch_supabase({"select_return": [_cover()]})
    with pytest.raises(HTTPException) as exc:
        await create_cover(
            _body(absent_employee_id="c001",
                  cover_employee_id="C009", cover_employee_name="Zed",
                  date_from="2026-10-10", date_to="2026-10-16"),
            current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 409


async def test_create_allows_touching_but_not_overlapping_ranges(patch_supabase):
    patch_supabase({"select_return": [_cover()], "insert_return": [_cover(id=7)]})
    result = await create_cover(
        _body(date_from="2026-10-13", date_to="2026-10-19"), current_user=CURRENT_USER
    )
    assert result["id"] == 7


async def test_create_allows_cover_for_a_different_person_or_rotation(patch_supabase):
    patch_supabase({"select_return": [_cover()], "insert_return": [_cover(id=8)]})
    other_person = await create_cover(
        _body(absent_employee_id="C003", absent_employee_name="Cara",
              date_from="2026-10-10", date_to="2026-10-16"),
        current_user=CURRENT_USER,
    )
    assert other_person["id"] == 8
    patch_supabase({"select_return": [_cover()], "insert_return": [_cover(id=9)]})
    other_kind = await create_cover(
        _body(kind="duty", date_from="2026-10-10", date_to="2026-10-16"),
        current_user=CURRENT_USER,
    )
    assert other_kind["id"] == 9


async def test_update_reason_only_skips_overlap_and_stamps_updated_at(patch_supabase):
    fake = patch_supabase({
        "select_return": [_cover()],
        "update_return": [_cover(reason="training")],
    })
    result = await update_cover(1, RotationCoverUpdate(reason="training"), current_user=CURRENT_USER)
    assert result["reason"] == "training"
    update_call = next(c for c in fake.state["calls"] if c["op"] == "update")
    assert update_call["payload"]["reason"] == "training"
    assert "updated_at" in update_call["payload"]


async def test_update_ignores_its_own_range(patch_supabase):
    patch_supabase({"select_return": [_cover()], "update_return": [_cover(reason="x")]})
    result = await update_cover(
        1,
        RotationCoverUpdate(date_from="2026-10-06", date_to="2026-10-12", reason="x"),
        current_user=CURRENT_USER,
    )
    assert result["reason"] == "x"


async def test_update_rejects_a_new_overlap(patch_supabase):
    clash = _cover(id=2, cover_employee_id="C009", cover_employee_name="Zed",
                   date_from="2026-10-13", date_to="2026-10-19")
    patch_supabase({"select_return": [_cover(), clash]})
    with pytest.raises(HTTPException) as exc:
        await update_cover(1, RotationCoverUpdate(date_to="2026-10-15"), current_user=CURRENT_USER)
    assert exc.value.status_code == 409
    assert "Zed" in exc.value.detail


async def test_update_rejects_partially_inverted_dates(patch_supabase):
    patch_supabase({"select_return": [_cover()]})
    with pytest.raises(HTTPException) as exc:
        await update_cover(1, RotationCoverUpdate(date_from="2026-10-20"), current_user=CURRENT_USER)
    assert exc.value.status_code == 422


async def test_update_rejects_covering_yourself(patch_supabase):
    patch_supabase({"select_return": [_cover()]})
    with pytest.raises(HTTPException) as exc:
        await update_cover(
            1, RotationCoverUpdate(cover_employee_id="C001"), current_user=CURRENT_USER
        )
    assert exc.value.status_code == 422


async def test_update_without_fields_returns_existing(patch_supabase):
    fake = patch_supabase({"select_return": [_cover()]})
    result = await update_cover(1, RotationCoverUpdate(), current_user=CURRENT_USER)
    assert result["id"] == 1
    assert all(c["op"] == "select" for c in fake.state["calls"])


async def test_delete_happy_path_and_404(patch_supabase):
    fake = patch_supabase({"select_return": [_cover()]})
    result = await delete_cover(1, current_user=CURRENT_USER)
    assert result["success"] is True
    assert any(c["op"] == "delete" for c in fake.state["calls"])
    patch_supabase({"select_return": []})
    with pytest.raises(HTTPException) as exc:
        await delete_cover(9, current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_update_model_rejects_both_dates_inverted(patch_supabase):
    patch_supabase({})
    with pytest.raises(ValidationError):
        RotationCoverUpdate(date_from="2026-10-12", date_to="2026-10-06")


async def test_update_kind_change_is_normalised_and_validated(patch_supabase):
    fake = patch_supabase({
        "select_return": [_cover()],
        "update_return": [_cover(kind="duty")],
    })
    await update_cover(1, RotationCoverUpdate(kind="Duty"), current_user=CURRENT_USER)
    update_call = next(c for c in fake.state["calls"] if c["op"] == "update")
    assert update_call["payload"]["kind"] == "duty"
    with pytest.raises(ValidationError):
        RotationCoverUpdate(kind="overtime")
    assert RotationCoverUpdate(kind=None).kind is None


async def test_create_allows_same_person_on_a_different_rotation(patch_supabase):
    patch_supabase({"select_return": [_cover()], "insert_return": [_cover(id=10)]})
    result = await create_cover(
        _body(rotation_id=9, date_from="2026-10-10", date_to="2026-10-16"),
        current_user=CURRENT_USER,
    )
    assert result["id"] == 10


async def test_overlap_tolerates_date_objects_from_the_db(patch_supabase):
    patch_supabase({"select_return": [_cover(date_from=date(2026, 10, 6), date_to=date(2026, 10, 12))]})
    with pytest.raises(HTTPException) as exc:
        await create_cover(
            _body(cover_employee_id="C009", cover_employee_name="Zed",
                  date_from="2026-10-10", date_to="2026-10-16"),
            current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 409


async def test_get_failure_is_a_500(patch_supabase):
    patch_supabase({"raise_op": "select", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await get_cover(1, current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_create_empty_insert_is_a_500(patch_supabase):
    patch_supabase({"select_return": [], "insert_return": None})
    with pytest.raises(HTTPException) as exc:
        await create_cover(_body(), current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_create_db_failure_is_a_500(patch_supabase):
    patch_supabase({"select_return": [], "raise_op": "insert", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await create_cover(_body(), current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_update_empty_result_is_a_500(patch_supabase):
    patch_supabase({"select_return": [_cover()], "update_return": None})
    with pytest.raises(HTTPException) as exc:
        await update_cover(1, RotationCoverUpdate(reason="x"), current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_update_db_failure_is_a_500(patch_supabase):
    patch_supabase({"select_return": [_cover()], "raise_op": "update", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await update_cover(1, RotationCoverUpdate(reason="x"), current_user=CURRENT_USER)
    assert exc.value.status_code == 500


async def test_delete_db_failure_is_a_500(patch_supabase):
    patch_supabase({"select_return": [_cover()], "raise_op": "delete", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await delete_cover(1, current_user=CURRENT_USER)
    assert exc.value.status_code == 500
