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
from app.routers.overtime import (
    OvertimeCreate, OvertimeUpdate,
    get_overtime, create_overtime, update_overtime, delete_overtime,
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
    assert "connection refused" in exc.value.detail


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
    assert "no data returned" in exc.value.detail


async def test_create_overtime_db_error_is_500(patch_supabase):
    patch_supabase({"raise_op": "insert", "raise_msg": "insert failed"})
    with pytest.raises(HTTPException) as exc:
        await create_overtime(_create_input(), current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    assert "insert failed" in exc.value.detail


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
    assert "connection lost" in exc.value.detail


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
    assert "Update failed" in exc.value.detail


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


async def test_update_overtime_non_status_field_edit_needs_no_role_check(patch_supabase):
    # Editing a plain field (not status: approved/rejected) must not go through the
    # manager-role gate at all — no Authorization header is needed.
    fake = patch_supabase({
        "select_return": [{"id": 7}],
        "update_return": [{"id": 7, "reason": "Updated reason", "spares_used": "[]"}],
    })

    result = await update_overtime(
        7, OvertimeUpdate(reason="Updated reason"), authorization=None, current_user=CURRENT_USER,
    )
    assert result["reason"] == "Updated reason"


# ─── delete_overtime ─────────────────────────────────────────────────────────────────

async def test_delete_overtime_happy_path(patch_supabase):
    fake = patch_supabase({"select_return": [{"id": 3}]})

    result = await delete_overtime(3, current_user=CURRENT_USER)

    assert result == {"success": True, "message": "Overtime deleted successfully"}
    delete_call = next(c for c in fake.state["calls"] if c["op"] == "delete")
    assert ("id", 3) in delete_call["filters"]


async def test_delete_overtime_not_found_is_404(patch_supabase):
    patch_supabase({"select_return": None})
    with pytest.raises(HTTPException) as exc:
        await delete_overtime(404, current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_delete_overtime_db_error_is_500(patch_supabase):
    patch_supabase({"select_return": [{"id": 3}], "raise_op": "delete", "raise_msg": "delete failed"})
    with pytest.raises(HTTPException) as exc:
        await delete_overtime(3, current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    assert "delete failed" in exc.value.detail
