# tests/test_overtime_crud.py — the route handlers behind overtime.py's CRUD surface
# (get_overtime, create_overtime, update_overtime, delete_overtime). overtime.py was
# 44% covered with these handlers entirely untested — only the Pydantic validation and
# the pure analytics helpers had tests. Uses the sanctioned "call the route coroutine
# directly against a fake supabase client" recipe (see test_documents_folder_rename.py),
# with a bespoke fake local to this file since overtime touches auth.py's role-gated
# approval path in addition to the overtime table itself.
#
# Every Query()/Form()-defaulted parameter is passed explicitly on every direct call —
# calling a route coroutine directly bypasses FastAPI's Depends()/Query() resolution,
# so an omitted parameter stays a raw sentinel object instead of its real default.

import json

import pytest
from fastapi import HTTPException

from app import auth as auth_mod
from app.routers import overtime as overtime_mod
from pydantic import ValidationError

from app.routers.overtime import (
    OvertimeCreate, OvertimeUpdate, BulkStatusUpdate, BulkEditUpdate, TakeAsLeaveInput,
    get_overtime, get_overtime_by_id, create_overtime, update_overtime, delete_overtime,
    bulk_update_overtime_status, bulk_edit_overtime, take_as_leave,
)


# ─── Fake supabase for the "overtime" table — select (get_or_404)/insert/update/delete ──

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table_name, state, cfg):
        self.table_name = table_name
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

    def in_(self, col, vals):
        self._filters.append((col, "in", vals))
        return self

    def lte(self, col, val):
        self._filters.append((col, "lte", val))
        return self

    def gte(self, col, val):
        self._filters.append((col, "gte", val))
        return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
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
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
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
        assert name == "overtime"
        return _Query(name, self.state, self.cfg)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _apply(cfg: dict) -> _FakeSupabase:
        fake = _FakeSupabase(cfg)
        monkeypatch.setattr(overtime_mod, "supabase", fake)
        return fake
    return _apply


# ─── Fake supabase for app.auth — the approve/reject role-gate on update_overtime ───────

class _FakeAuthClient:
    def __init__(self, user):
        self._user = user

    def get_user(self, token):
        class R: pass
        r = R()
        r.user = self._user
        return r


class _FakeAuthRpcResult:
    def __init__(self, data):
        self.data = data


class _FakeAuthSupabase:
    def __init__(self, user, role):
        self.auth = _FakeAuthClient(user)
        self._role = role

    def rpc(self, name, params):
        return self

    def execute(self):
        return _FakeAuthRpcResult(self._role)


class _FakeAuthUser:
    def __init__(self, uid="u-1", email="approver@x.com"):
        self.id = uid
        self.email = email
        self.factors = []  # real get_user populates this; [] = not MFA-enrolled


@pytest.fixture
def patch_auth(monkeypatch):
    def _apply(role: str):
        monkeypatch.setattr(auth_mod, "supabase", _FakeAuthSupabase(_FakeAuthUser(), role))
    return _apply


CURRENT_USER = {"user_id": "u1", "email": "u1@x.com", "role": "user"}


def _create_input(**overrides):
    base = dict(
        employee_name="Alice Smith", employee_id="E1", position="Fitter",
        overtime_type="regular", date="2024-01-01", hours=3.0,
    )
    base.update(overrides)
    return OvertimeCreate(**base)


# ─── get_overtime ────────────────────────────────────────────────────────────────────

# get_overtime's pagination loop needs its own fake — it calls .range() repeatedly and
# must stop only once a batch comes back shorter than the 1000-row page size (this loop
# exists specifically because a single unbounded .execute() used to silently drop every
# record past Supabase/PostgREST's 1000-row cap).

class _PagingQuery:
    def __init__(self, state, pages):
        self.state = state
        self.pages = pages
        self._filters = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        self.state["last_filters"] = list(self._filters)
        return self

    def order(self, *a, **k):
        return self

    def range(self, start, end):
        self.state.setdefault("ranges", []).append((start, end))
        return self

    def execute(self):
        idx = len(self.state["ranges"]) - 1
        page = self.pages[idx] if idx < len(self.pages) else []
        return _Resp(page)


class _PagingSupabase:
    def __init__(self, pages):
        self.state = {"ranges": []}
        self.pages = pages

    def table(self, name):
        assert name == "overtime"
        return _PagingQuery(self.state, self.pages)


async def test_get_overtime_returns_decoded_records(monkeypatch):
    row = {"id": 1, "employee_name": "Alice", "spares_used": json.dumps([{"name": "Bolt"}])}
    fake = _PagingSupabase(pages=[[row]])
    monkeypatch.setattr(overtime_mod, "supabase", fake)

    result = await get_overtime(status=None, overtime_type=None)

    assert result == [{"id": 1, "employee_name": "Alice", "spares_used": [{"name": "Bolt"}]}]


async def test_get_overtime_applies_status_and_type_filters(monkeypatch):
    fake = _PagingSupabase(pages=[[]])
    monkeypatch.setattr(overtime_mod, "supabase", fake)

    await get_overtime(status="pending", overtime_type="weekend")

    assert ("status", "pending") in fake.state["last_filters"]
    assert ("overtime_type", "weekend") in fake.state["last_filters"]


async def test_get_overtime_narrows_to_one_person(monkeypatch):
    # The Personnel detail reads one person's overtime this way.
    fake = _PagingSupabase(pages=[[]])
    monkeypatch.setattr(overtime_mod, "supabase", fake)
    await get_overtime(status=None, overtime_type=None, employee_id="C0106")
    assert ("employee_id", "C0106") in fake.state["last_filters"]


async def test_get_overtime_paginates_past_the_1000_row_page_cap(monkeypatch):
    # A first page exactly at PAGE (1000) must trigger a second .range() call; a page
    # shorter than 1000 must stop the loop. This is the direct regression test for the
    # bug this loop exists to prevent (older records silently invisible past row 1000).
    page1 = [{"id": i, "spares_used": None} for i in range(1000)]
    page2 = [{"id": 1000, "spares_used": None}, {"id": 1001, "spares_used": None}]
    fake = _PagingSupabase(pages=[page1, page2])
    monkeypatch.setattr(overtime_mod, "supabase", fake)

    result = await get_overtime(status=None, overtime_type=None)

    assert len(result) == 1002
    assert fake.state["ranges"] == [(0, 999), (1000, 1999)]


async def test_get_overtime_stops_after_a_short_page(monkeypatch):
    page1 = [{"id": 1, "spares_used": None}, {"id": 2, "spares_used": None}]
    fake = _PagingSupabase(pages=[page1, [{"id": 999, "spares_used": None}]])
    monkeypatch.setattr(overtime_mod, "supabase", fake)

    result = await get_overtime(status=None, overtime_type=None)

    # Only the first (short) page is fetched — a second .range() call must not happen.
    assert len(result) == 2
    assert fake.state["ranges"] == [(0, 999)]


async def test_get_overtime_db_error_is_500(monkeypatch):
    class _RaisingSupabase:
        def table(self, name):
            raise Exception("connection refused")
    monkeypatch.setattr(overtime_mod, "supabase", _RaisingSupabase())

    with pytest.raises(HTTPException) as exc:
        await get_overtime(status=None, overtime_type=None)
    assert exc.value.status_code == 500
    # 500s are generic since the server_error codemod: no internal text leaks.
    assert "connection refused" not in exc.value.detail
    assert exc.value.detail.startswith("Loading overtime failed. Please try again. (ref ")


# ─── create_overtime ─────────────────────────────────────────────────────────────────

async def test_create_overtime_inserts_expected_shape(patch_supabase):
    fake = patch_supabase({"insert_return": [{"id": 5, "employee_name": "Alice Smith", "spares_used": "[]"}]})

    result = await create_overtime(_create_input(), current_user=CURRENT_USER)

    assert result == {"id": 5, "employee_name": "Alice Smith", "spares_used": []}
    insert_call = next(c for c in fake.state["calls"] if c["op"] == "insert")
    payload = insert_call["payload"]
    assert payload["status"] == "pending"
    assert isinstance(payload["applied_date"], str) and payload["applied_date"]
    assert payload["spares_used"] == "[]"  # encoded to a JSON string, not left as a list


async def test_create_overtime_department_none_is_still_sent_not_dropped(patch_supabase):
    # Regression coverage for the bug documented on OvertimeCreate.department: the field
    # was previously undeclared and silently dropped on every save. It must now always
    # be present in the insert payload, even when the caller didn't supply it.
    fake = patch_supabase({"insert_return": [{"id": 1, "spares_used": "[]"}]})

    await create_overtime(_create_input(), current_user=CURRENT_USER)

    payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert "department" in payload
    assert payload["department"] is None


async def test_create_overtime_department_provided_is_passed_through(patch_supabase):
    fake = patch_supabase({"insert_return": [{"id": 1, "spares_used": "[]"}]})

    await create_overtime(_create_input(department="Processing"), current_user=CURRENT_USER)

    payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert payload["department"] == "Processing"


async def test_create_overtime_notes_provided_is_passed_through_and_read_back(patch_supabase):
    fake = patch_supabase({"insert_return": [{"id": 1, "notes": "Gate 4 roster", "spares_used": "[]"}]})

    result = await create_overtime(_create_input(notes="Gate 4 roster"), current_user=CURRENT_USER)

    payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert payload["notes"] == "Gate 4 roster"
    assert result["notes"] == "Gate 4 roster"


async def test_create_overtime_notes_none_is_still_sent_not_dropped(patch_supabase):
    # Same bug class as department: an undeclared field never reaches the insert.
    fake = patch_supabase({"insert_return": [{"id": 1, "spares_used": "[]"}]})

    await create_overtime(_create_input(), current_user=CURRENT_USER)

    payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert "notes" in payload
    assert payload["notes"] is None


async def test_create_overtime_defaults_cost_centre_to_engineering(patch_supabase):
    fake = patch_supabase({"insert_return": [{"id": 1, "spares_used": "[]"}]})

    await create_overtime(_create_input(), current_user=CURRENT_USER)

    payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert payload["cost_centre"] == "Engineering"


async def test_create_overtime_preserves_cross_department_cost_centre(patch_supabase):
    fake = patch_supabase({"insert_return": [{"id": 1, "spares_used": "[]"}]})

    await create_overtime(_create_input(cost_centre="  Projects  "), current_user=CURRENT_USER)

    payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert payload["cost_centre"] == "Projects"


async def test_create_overtime_encodes_spares_used(patch_supabase):
    fake = patch_supabase({"insert_return": [{"id": 1, "spares_used": "[]"}]})
    spares = [{"name": "Grease", "unit_price": 12.5}]

    await create_overtime(_create_input(spares_used=spares), current_user=CURRENT_USER)

    payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert payload["spares_used"] == json.dumps(spares)


async def test_create_overtime_no_data_returned_is_500(patch_supabase):
    patch_supabase({"insert_return": []})
    with pytest.raises(HTTPException) as exc:
        await create_overtime(_create_input(), current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    # 500s are generic since the server_error codemod: no internal text leaks.
    assert "no data returned" not in exc.value.detail
    assert exc.value.detail.startswith("Creating overtime failed. Please try again. (ref ")


async def test_create_overtime_db_error_is_500(patch_supabase):
    patch_supabase({"raise_op": "insert", "raise_msg": "insert failed"})
    with pytest.raises(HTTPException) as exc:
        await create_overtime(_create_input(), current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    # 500s are generic since the server_error codemod: no internal text leaks.
    assert "insert failed" not in exc.value.detail
    assert exc.value.detail.startswith("Creating overtime failed. Please try again. (ref ")


# ─── update_overtime ─────────────────────────────────────────────────────────────────

async def test_update_overtime_happy_path(patch_supabase):
    fake = patch_supabase({
        "select_return": [{"id": 7, "employee_name": "Alice"}],
        "update_return": [{"id": 7, "employee_name": "Alice Updated", "spares_used": "[]"}],
    })

    result = await update_overtime(
        7, OvertimeUpdate(employee_name="Alice Updated"),
        authorization=None, current_user=CURRENT_USER,
    )

    assert result == {"id": 7, "employee_name": "Alice Updated", "spares_used": []}
    update_call = next(c for c in fake.state["calls"] if c["op"] == "update")
    assert update_call["payload"] == {"employee_name": "Alice Updated"}  # exclude_unset


async def test_update_overtime_exclude_unset_not_none_filter(patch_supabase):
    # Regression coverage for the null-vs-unset PATCH bug: an explicit `reason: null`
    # must reach the update payload (clearing the field), not be silently dropped the
    # way an `is not None` filter would drop it.
    fake = patch_supabase({
        "select_return": [{"id": 7}],
        "update_return": [{"id": 7, "reason": None, "spares_used": "[]"}],
    })

    await update_overtime(
        7, OvertimeUpdate(reason=None, employee_name="X"),
        authorization=None, current_user=CURRENT_USER,
    )

    payload = next(c for c in fake.state["calls"] if c["op"] == "update")["payload"]
    # `reason` was explicitly set to None in the OvertimeUpdate constructor call above,
    # so it counts as "set" for exclude_unset and must appear in the payload.
    assert "reason" in payload and payload["reason"] is None


async def test_update_overtime_notes_patch_is_applied_and_read_back(patch_supabase):
    fake = patch_supabase({
        "select_return": [{"id": 7}],
        "update_return": [{"id": 7, "notes": "Shift swapped", "spares_used": "[]"}],
    })

    result = await update_overtime(
        7, OvertimeUpdate(notes="Shift swapped"),
        authorization=None, current_user=CURRENT_USER,
    )

    payload = next(c for c in fake.state["calls"] if c["op"] == "update")["payload"]
    assert payload == {"notes": "Shift swapped"}
    assert result["notes"] == "Shift swapped"


async def test_update_overtime_notes_null_clears(patch_supabase):
    # exclude_unset, not a None-filter: an explicit null must clear notes.
    fake = patch_supabase({
        "select_return": [{"id": 7}],
        "update_return": [{"id": 7, "notes": None, "spares_used": "[]"}],
    })

    await update_overtime(
        7, OvertimeUpdate(notes=None, employee_name="X"),
        authorization=None, current_user=CURRENT_USER,
    )

    payload = next(c for c in fake.state["calls"] if c["op"] == "update")["payload"]
    assert "notes" in payload and payload["notes"] is None


async def test_update_overtime_normalizes_blank_cost_centre_to_engineering(patch_supabase):
    fake = patch_supabase({
        "select_return": [{"id": 7}],
        "update_return": [{"id": 7, "cost_centre": "Engineering", "spares_used": "[]"}],
    })

    await update_overtime(
        7, OvertimeUpdate(cost_centre="  "),
        authorization=None, current_user=CURRENT_USER,
    )

    payload = next(c for c in fake.state["calls"] if c["op"] == "update")["payload"]
    assert payload["cost_centre"] == "Engineering"


async def test_update_overtime_encodes_spares_used_only_when_sent(patch_supabase):
    fake = patch_supabase({
        "select_return": [{"id": 7}],
        "update_return": [{"id": 7, "spares_used": "[]"}],
    })

    await update_overtime(
        7, OvertimeUpdate(spares_used=[{"name": "Belt"}]),
        authorization=None, current_user=CURRENT_USER,
    )

    payload = next(c for c in fake.state["calls"] if c["op"] == "update")["payload"]
    assert payload["spares_used"] == json.dumps([{"name": "Belt"}])


async def test_update_overtime_db_error_is_500(patch_supabase):
    patch_supabase({
        "select_return": [{"id": 7}],
        "raise_op": "update", "raise_msg": "connection lost",
    })
    with pytest.raises(HTTPException) as exc:
        await update_overtime(7, OvertimeUpdate(reason="x"), authorization=None, current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    # 500s are generic since the server_error codemod: no internal text leaks.
    assert "connection lost" not in exc.value.detail
    assert exc.value.detail.startswith("Updating overtime failed. Please try again. (ref ")


async def test_update_overtime_not_found_is_404(patch_supabase):
    patch_supabase({"select_return": None})
    with pytest.raises(HTTPException) as exc:
        await update_overtime(999, OvertimeUpdate(reason="x"), authorization=None, current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_update_overtime_update_failed_is_500(patch_supabase):
    patch_supabase({"select_return": [{"id": 7}], "update_return": []})
    with pytest.raises(HTTPException) as exc:
        await update_overtime(7, OvertimeUpdate(reason="x"), authorization=None, current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    assert exc.value.detail.startswith("Updating overtime failed. Please try again. (ref ")


async def test_update_overtime_approve_without_authorization_is_401(patch_supabase):
    patch_supabase({"select_return": [{"id": 7}], "update_return": [{"id": 7}]})
    with pytest.raises(HTTPException) as exc:
        await update_overtime(7, OvertimeUpdate(status="approved"), authorization=None, current_user=CURRENT_USER)
    assert exc.value.status_code == 401


async def test_update_overtime_approve_with_insufficient_role_is_403(patch_supabase, patch_auth):
    patch_supabase({"select_return": [{"id": 7}], "update_return": [{"id": 7}]})
    patch_auth(role="user")
    with pytest.raises(HTTPException) as exc:
        await update_overtime(
            7, OvertimeUpdate(status="approved"), authorization="Bearer good-token", current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 403


async def test_update_overtime_reject_with_insufficient_role_is_403(patch_supabase, patch_auth):
    # Both trigger statuses (approved AND rejected) must go through the same gate.
    patch_supabase({"select_return": [{"id": 7}], "update_return": [{"id": 7}]})
    patch_auth(role="viewer")
    with pytest.raises(HTTPException) as exc:
        await update_overtime(
            7, OvertimeUpdate(status="rejected"), authorization="Bearer good-token", current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 403


async def test_update_overtime_approve_with_manager_role_succeeds(patch_supabase, patch_auth):
    fake = patch_supabase({
        "select_return": [{"id": 7}],
        "update_return": [{"id": 7, "status": "approved", "spares_used": "[]"}],
    })
    patch_auth(role="manager")

    result = await update_overtime(
        7, OvertimeUpdate(status="approved"), authorization="Bearer good-token", current_user=CURRENT_USER,
    )

    assert result["status"] == "approved"


async def test_update_overtime_status_paid_without_manager_is_403(patch_supabase, patch_auth):
    patch_supabase({"select_return": [{"id": 7}]})
    patch_auth(role="viewer")
    with pytest.raises(HTTPException) as exc:
        await update_overtime(
            7,
            OvertimeUpdate(status="paid", approved_by="Someone Else"),
            authorization="Bearer good-token",
            current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 403


async def test_update_overtime_non_status_field_edit_on_pending_needs_no_role_check(patch_supabase):
    # Pending records: plain field edits do not require manager role.
    fake = patch_supabase({
        "select_return": [{"id": 7, "status": "pending"}],
        "update_return": [{"id": 7, "reason": "Updated reason", "spares_used": "[]"}],
    })

    result = await update_overtime(
        7, OvertimeUpdate(reason="Updated reason"), authorization=None, current_user=CURRENT_USER,
    )
    assert result["reason"] == "Updated reason"


async def test_update_overtime_hours_on_approved_record_requires_manager(patch_supabase, patch_auth):
    patch_supabase({"select_return": [{"id": 7, "status": "approved"}]})
    patch_auth(role="viewer")
    with pytest.raises(HTTPException) as exc:
        await update_overtime(
            7,
            OvertimeUpdate(hours=20),
            authorization="Bearer good-token",
            current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 403


# ─── delete_overtime ─────────────────────────────────────────────────────────────────

async def test_delete_overtime_happy_path(patch_supabase, patch_auth):
    fake = patch_supabase({"select_return": [{"id": 3}]})
    patch_auth(role="manager")

    result = await delete_overtime(3, authorization="Bearer good-token", current_user=CURRENT_USER)

    assert result == {"success": True, "message": "Overtime deleted successfully"}
    delete_call = next(c for c in fake.state["calls"] if c["op"] == "delete")
    assert ("id", 3) in delete_call["filters"]


async def test_delete_overtime_not_found_is_404(patch_supabase, patch_auth):
    patch_supabase({"select_return": None})
    patch_auth(role="manager")
    with pytest.raises(HTTPException) as exc:
        await delete_overtime(404, authorization="Bearer good-token", current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_delete_overtime_db_error_is_500(patch_supabase, patch_auth):
    patch_supabase({"select_return": [{"id": 3}], "raise_op": "delete", "raise_msg": "delete failed"})
    patch_auth(role="manager")
    with pytest.raises(HTTPException) as exc:
        await delete_overtime(3, authorization="Bearer good-token", current_user=CURRENT_USER)
    assert exc.value.status_code == 500


# ─── bulk_update_overtime_status ─────────────────────────────────────────────────────

async def test_bulk_update_overtime_status_approve_happy_path(patch_supabase, patch_auth):
    fake = patch_supabase({
        "update_return": [
            {"id": 1, "status": "approved", "approved_by": "Mgr", "spares_used": None},
            {"id": 2, "status": "approved", "approved_by": "Mgr", "spares_used": None},
        ],
    })
    patch_auth(role="manager")

    result = await bulk_update_overtime_status(
        BulkStatusUpdate(ids=[1, 2, 3], status="approved", approved_by="Mgr", approved_at="2024-01-01T12:00:00Z"),
        authorization="Bearer good-token", current_user=CURRENT_USER,
    )

    assert result.succeeded == 2
    assert result.failed == 1
    assert len(result.updated) == 2
    update_call = next(c for c in fake.state["calls"] if c["op"] == "update")
    assert update_call["payload"]["status"] == "approved"
    # The approver is the signed-in person, whatever the browser claims ("Mgr" was sent and is ignored).
    assert update_call["payload"]["approved_by"] == CURRENT_USER["email"]
    assert update_call["payload"]["approved_at"]
    assert ("id", "in", [1, 2, 3]) in update_call["filters"]
    assert ("status", "pending") in update_call["filters"]


async def test_bulk_update_overtime_status_reject_without_manager_is_403(patch_supabase, patch_auth):
    patch_supabase({"update_return": []})
    patch_auth(role="user")
    with pytest.raises(HTTPException) as exc:
        await bulk_update_overtime_status(
            BulkStatusUpdate(ids=[5], status="rejected", rejected_by="Mgr"),
            authorization="Bearer good-token", current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 403


async def test_bulk_update_overtime_status_db_error_is_500(patch_supabase, patch_auth):
    patch_supabase({"raise_op": "update", "raise_msg": "bulk failed"})
    patch_auth(role="manager")
    with pytest.raises(HTTPException) as exc:
        await bulk_update_overtime_status(
            BulkStatusUpdate(ids=[1], status="approved", approved_by="Mgr"),
            authorization="Bearer good-token", current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 500
    # 500s are generic since the server_error codemod: no internal text leaks.
    assert "bulk failed" not in exc.value.detail
    assert exc.value.detail.startswith("Bulk-updating overtime failed. Please try again. (ref ")

# ─── bulk_edit_overtime ──────────────────────────────────────────────────────────

async def test_bulk_edit_updates_pending_records_with_only_the_patch(patch_supabase):
    fake = patch_supabase({
        "select_return": [{"id": 1, "status": "pending"}, {"id": 2, "status": "pending"}],
        "update_return": [{"id": 1, "cost_centre": "Projects"}, {"id": 2, "cost_centre": "Projects"}],
    })

    result = await bulk_edit_overtime(
        BulkEditUpdate(ids=[1, 2], cost_centre="Projects"), authorization=None, current_user=CURRENT_USER,
    )

    assert result.succeeded == 2 and result.failed == 0 and result.errors == []
    update_call = next(c for c in fake.state["calls"] if c["op"] == "update")
    assert update_call["payload"] == {"cost_centre": "Projects"}
    assert ("id", "in", [1, 2]) in update_call["filters"]


async def test_bulk_edit_skips_locked_records_for_non_manager(patch_supabase, patch_auth):
    fake = patch_supabase({
        "select_return": [{"id": 1, "status": "approved"}, {"id": 2, "status": "pending"}],
        "update_return": [{"id": 2, "notes": "crew B"}],
    })
    patch_auth(role="viewer")

    result = await bulk_edit_overtime(
        BulkEditUpdate(ids=[1, 2], notes="crew B"),
        authorization="Bearer [REDACTED]", current_user=CURRENT_USER,
    )

    assert result.succeeded == 1 and result.failed == 1
    assert any("#1" in e and "approved" in e for e in result.errors)
    update_call = next(c for c in fake.state["calls"] if c["op"] == "update")
    assert ("id", "in", [2]) in update_call["filters"]


async def test_bulk_edit_manager_edits_locked_records_too(patch_supabase, patch_auth):
    fake = patch_supabase({
        "select_return": [{"id": 1, "status": "rejected"}],
        "update_return": [{"id": 1, "reason": "corrected"}],
    })
    patch_auth(role="manager")

    result = await bulk_edit_overtime(
        BulkEditUpdate(ids=[1], reason="corrected"),
        authorization="Bearer [REDACTED]", current_user=CURRENT_USER,
    )

    assert result.succeeded == 1 and result.failed == 0


async def test_bulk_edit_empty_patch_is_422(patch_supabase):
    patch_supabase({"select_return": []})
    with pytest.raises(HTTPException) as exc:
        await bulk_edit_overtime(BulkEditUpdate(ids=[1]), authorization=None, current_user=CURRENT_USER)
    assert exc.value.status_code == 422


async def test_bulk_edit_lifecycle_field_is_rejected_loudly():
    # status must never ride a bulk edit past the signature gate — and a mistyped
    # field must 422, not silently edit nothing.
    with pytest.raises(ValidationError):
        BulkEditUpdate(ids=[1], status="approved")
    with pytest.raises(ValidationError):
        BulkEditUpdate(ids=[1], cost_center="Projects")


async def test_bulk_edit_missing_ids_are_reported_not_silent(patch_supabase):
    patch_supabase({
        "select_return": [{"id": 2, "status": "pending"}],
        "update_return": [{"id": 2, "notes": "x"}],
    })
    result = await bulk_edit_overtime(
        BulkEditUpdate(ids=[1, 2], notes="x"), authorization=None, current_user=CURRENT_USER,
    )
    assert result.succeeded == 1 and result.failed == 1
    assert any("#1" in e for e in result.errors)


# ─── take-as-leave: approved lieu OT → one linked approved leave ────────────────

class _TakeSupabase:
    """Two-table fake: the overtime lookup plus the leaves lookup/insert, recorded."""

    def __init__(self, overtime_rows, leaves_rows, created_leave):
        self.state = {"calls": []}
        self.cfgs = {
            "overtime": {"select_return": overtime_rows},
            "leaves": {"select_return": leaves_rows, "insert_return": [created_leave] if created_leave else []},
        }

    def table(self, name):
        return _Query(name, self.state, self.cfgs[name])


def _take_input(**overrides):
    base = dict(start_date="2024-02-01", end_date="2024-02-01")
    base.update(overrides)
    return TakeAsLeaveInput(**base)


def _lieu_ot(**overrides):
    base = dict(id=7, employee_id="E1", employee_name="Alice Smith", position="Fitter",
                overtime_type="regular", date="2024-01-15", status="approved", payout_method="lieu")
    base.update(overrides)
    return base


async def test_take_as_leave_creates_an_approved_linked_leave(monkeypatch, patch_auth):
    patch_auth("manager")
    created = {"id": 9, "overtime_id": 7, "status": "approved"}
    fake = _TakeSupabase([_lieu_ot()], [], created)
    monkeypatch.setattr(overtime_mod, "supabase", fake)
    result = await take_as_leave(7, _take_input(), authorization="Bearer [REDACTED]", current_user=CURRENT_USER)
    assert result["created"] is True
    assert result["leave"]["overtime_id"] == 7
    inserts = [c for c in fake.state["calls"] if c["op"] == "insert"]
    assert len(inserts) == 1 and inserts[0]["table"] == "leaves"
    payload = inserts[0]["payload"]
    assert payload["leave_type"] == "lieu"  # the stored key, like a manual lieu leave
    assert payload["status"] == "approved"
    assert payload["overtime_id"] == 7
    assert payload["employee_id"] == "E1" and payload["employee_name"] == "Alice Smith"
    assert payload["start_date"] == "2024-02-01" and payload["end_date"] == "2024-02-01"


async def test_take_as_leave_is_idempotent_when_the_leave_already_exists(monkeypatch, patch_auth):
    patch_auth("manager")
    existing = {"id": 9, "overtime_id": 7, "status": "approved", "start_date": "2024-02-01"}
    fake = _TakeSupabase([_lieu_ot()], [existing], None)
    monkeypatch.setattr(overtime_mod, "supabase", fake)
    result = await take_as_leave(7, _take_input(), authorization="Bearer [REDACTED]", current_user=CURRENT_USER)
    assert result["created"] is False
    assert result["leave"] == existing
    assert [c for c in fake.state["calls"] if c["op"] == "insert"] == []


async def test_take_as_leave_rejects_anything_but_approved_lieu(monkeypatch, patch_auth):
    patch_auth("manager")
    for ot in (_lieu_ot(payout_method="cash"), _lieu_ot(status="pending"), _lieu_ot(status="rejected")):
        fake = _TakeSupabase([ot], [], None)
        monkeypatch.setattr(overtime_mod, "supabase", fake)
        with pytest.raises(HTTPException) as exc:
            await take_as_leave(7, _take_input(), authorization="Bearer [REDACTED]", current_user=CURRENT_USER)
        assert exc.value.status_code == 422
        assert [c for c in fake.state["calls"] if c["op"] == "insert"] == []


async def test_take_as_leave_rejects_a_backwards_range(monkeypatch, patch_auth):
    patch_auth("manager")
    fake = _TakeSupabase([_lieu_ot()], [], None)
    monkeypatch.setattr(overtime_mod, "supabase", fake)
    with pytest.raises(HTTPException) as exc:
        await take_as_leave(7, _take_input(start_date="2024-02-02", end_date="2024-02-01"),
                            authorization="Bearer [REDACTED]", current_user=CURRENT_USER)
    assert exc.value.status_code == 422


async def test_take_as_leave_missing_overtime_is_404(monkeypatch, patch_auth):
    patch_auth("manager")
    fake = _TakeSupabase([], [], None)
    monkeypatch.setattr(overtime_mod, "supabase", fake)
    with pytest.raises(HTTPException) as exc:
        await take_as_leave(999, _take_input(), authorization="Bearer [REDACTED]", current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_take_as_leave_needs_a_manager(monkeypatch, patch_auth):
    fake = _TakeSupabase([_lieu_ot()], [], None)
    monkeypatch.setattr(overtime_mod, "supabase", fake)
    with pytest.raises(HTTPException) as exc:
        await take_as_leave(7, _take_input(), authorization=None, current_user=CURRENT_USER)
    assert exc.value.status_code == 401
    patch_auth("user")
    with pytest.raises(HTTPException) as exc:
        await take_as_leave(7, _take_input(), authorization="Bearer [REDACTED]", current_user=CURRENT_USER)
    assert exc.value.status_code == 403
    assert [c for c in fake.state["calls"] if c["op"] == "insert"] == []



# ─── who approved is the session, a stale edit is refused, approved overtime is not deleted ────────────────────────

async def test_update_overtime_approval_is_stamped_from_the_session_not_the_browser(patch_supabase, patch_auth):
    fake = patch_supabase({"select_return": [{"id": 7, "status": "pending"}], "update_return": [{"id": 7, "status": "approved", "spares_used": None}]})
    patch_auth(role="manager")
    user = {**CURRENT_USER, "name": "Edson Mavhondo"}
    await update_overtime(7, OvertimeUpdate(status="approved", approved_by="Someone Else", approved_at="1999-01-01"), authorization="Bearer t", current_user=user)
    payload = next(c for c in fake.state["calls"] if c["op"] == "update")["payload"]
    assert payload["approved_by"] == "Edson Mavhondo" and payload["approved_at"] != "1999-01-01"


async def test_update_overtime_rejection_is_stamped_from_the_session(patch_supabase, patch_auth):
    fake = patch_supabase({"select_return": [{"id": 7, "status": "pending"}], "update_return": [{"id": 7, "status": "rejected", "spares_used": None}]})
    patch_auth(role="manager")
    await update_overtime(7, OvertimeUpdate(status="rejected"), authorization="Bearer t", current_user={**CURRENT_USER, "name": "Elias Mwazha"})
    payload = next(c for c in fake.state["calls"] if c["op"] == "update")["payload"]
    assert payload["rejected_by"] == "Elias Mwazha" and "approved_by" not in payload


async def test_update_overtime_refuses_a_save_made_from_a_stale_copy(patch_supabase):
    fake = patch_supabase({"select_return": [{"id": 7, "status": "pending", "updated_at": "2026-10-19T08:00:00+00:00"}], "update_return": [{"id": 7}]})
    with pytest.raises(HTTPException) as exc:
        await update_overtime(7, OvertimeUpdate(reason="mine", expected_updated_at="2026-10-19T07:00:00+00:00"), authorization=None, current_user=CURRENT_USER)
    assert exc.value.status_code == 409 and "Someone else changed" in exc.value.detail
    assert [c for c in fake.state["calls"] if c["op"] == "update"] == []


async def test_update_overtime_accepts_a_save_from_the_current_copy_and_moves_updated_at(patch_supabase):
    fake = patch_supabase({"select_return": [{"id": 7, "status": "pending", "updated_at": "2026-10-19T08:00:00+00:00"}], "update_return": [{"id": 7, "spares_used": None}]})
    await update_overtime(7, OvertimeUpdate(reason="mine", expected_updated_at="2026-10-19T08:00:00Z"), authorization=None, current_user=CURRENT_USER)
    payload = next(c for c in fake.state["calls"] if c["op"] == "update")["payload"]
    assert "expected_updated_at" not in payload and payload["updated_at"] > "2026-10-19T08:00:00+00:00"


@pytest.mark.parametrize("status", ["approved", "paid"])
async def test_delete_overtime_refuses_approved_or_paid(patch_supabase, patch_auth, status):
    fake = patch_supabase({"select_return": [{"id": 3, "status": status}]})
    patch_auth(role="manager")
    with pytest.raises(HTTPException) as exc:
        await delete_overtime(3, authorization="Bearer t", current_user=CURRENT_USER)
    assert exc.value.status_code == 409 and "Cancel it instead" in exc.value.detail
    assert [c for c in fake.state["calls"] if c["op"] == "delete"] == []


async def test_get_overtime_by_id_returns_the_decoded_row(patch_supabase):
    fake = patch_supabase({"select_return": [{"id": 7, "employee_name": "Ann", "spares_used": json.dumps([{"name": "Bolt"}])}]})
    row = await get_overtime_by_id(7)
    assert row == {"id": 7, "employee_name": "Ann", "spares_used": [{"name": "Bolt"}]}
    assert ("id", 7) in fake.state["calls"][0]["filters"]


async def test_get_overtime_by_id_missing_is_404(patch_supabase):
    patch_supabase({"select_return": None})
    with pytest.raises(HTTPException) as exc:
        await get_overtime_by_id(999)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Overtime not found"


async def test_get_overtime_by_id_db_error_is_500(patch_supabase):
    patch_supabase({"select_return": [{"id": 7}], "raise_op": "select"})
    with pytest.raises(HTTPException) as exc:
        await get_overtime_by_id(7)
    assert exc.value.status_code == 500
    assert exc.value.detail.startswith("Loading overtime failed. Please try again. (ref ")


async def test_bulk_edit_times_travel_as_a_valid_pair():
    # One time without the other would leave a request half-moved.
    with pytest.raises(ValidationError):
        BulkEditUpdate(ids=[1], start_time="17:00")
    with pytest.raises(ValidationError):
        BulkEditUpdate(ids=[1], end_time="20:00")
    # A zero-length window is no hours at all; a garbled clock time never reaches the database.
    with pytest.raises(ValidationError):
        BulkEditUpdate(ids=[1], start_time="17:00", end_time="17:00")
    with pytest.raises(ValidationError):
        BulkEditUpdate(ids=[1], start_time="25:00", end_time="20:00")
    with pytest.raises(ValidationError):
        BulkEditUpdate(ids=[1], start_time="5pm", end_time="20:00")
    # Overnight is fine (22:00 -> 06:00 is 8 hours), and so are seconds from a time input.
    assert BulkEditUpdate(ids=[1], start_time="22:00", end_time="06:00").end_time == "06:00"
    assert BulkEditUpdate(ids=[1], start_time="17:00:00", end_time="20:30:00").start_time == "17:00:00"
    # Hours still cannot be bulk-typed directly.
    with pytest.raises(ValidationError):
        BulkEditUpdate(ids=[1], hours=3)
