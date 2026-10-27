# tests/test_maintenance_m8.py - M8: the manager-only sign-off gate on PATCH
# and the bell's list slices (overdue / awaiting_signoff / mine). Same recipe
# as test_maintenance_close.py: call the route coroutine directly against a
# fake supabase client.
import pytest
from fastapi import HTTPException

import app.routers.maintenance as m
from app.routers.maintenance import (
    WorkOrderUpdate,
    get_work_orders,
    update_work_order,
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
        table = self.state.setdefault(self.table_name, [])
        matches = [r for r in table if all(r.get(c) == v for c, v in self._filters)]
        if self._mode == "insert":
            row = dict(self._payload)
            row["id"] = len(table) + 1
            table.append(row)
            return _Resp([row])
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

    def _patch(work_orders=None):
        state["work_orders"] = list(work_orders or [])
        monkeypatch.setattr(m, "supabase", _FakeSupabase(state))
        monkeypatch.setattr(m, "has_columns", lambda *a, **k: True)
        return state

    return _patch


def _user(**over):
    base = {"user_id": "u-1", "email": "alex@mine.test", "name": "Alex Moyo", "role": "user"}
    base.update(over)
    return base


def _row(**over):
    base = {
        "id": 1, "work_order_number": "WO-00001", "equipment_info": "Pump A",
        "status": "pending", "priority": "medium", "progress": 0,
        "allocated_to": "Alex Moyo", "artisan_name": "", "date_raised": "2026-10-20",
        "due_date": "2026-10-28", "foreman_name": "", "foreman_sign": "",
        "foreman_date": "", "classification": "planned_maintenance",
    }
    base.update(over)
    return base


# --- The sign-off gate ---

async def test_user_edits_anything_but_the_signoff(patch_supabase):
    patch_supabase([_row()])
    saved = await update_work_order(1, WorkOrderUpdate(progress=40, notes="Halfway there."), _user())
    assert saved["progress"] == 40


async def test_user_signing_is_refused_and_changes_nothing(patch_supabase):
    state = patch_supabase([_row()])
    with pytest.raises(HTTPException) as exc:
        await update_work_order(1, WorkOrderUpdate(foreman_sign="data:image/png,AAA"), _user())
    assert exc.value.status_code == 403
    assert state["work_orders"][0]["foreman_sign"] == ""


async def test_manager_signs_and_the_stamp_fires(patch_supabase):
    patch_supabase([_row()])
    saved = await update_work_order(
        1,
        WorkOrderUpdate(foreman_name="Lee Jones", foreman_sign="data:image/png,AAA", foreman_date="2026-10-26"),
        _user(role="manager"),
    )
    assert saved["foreman_sign"] == "data:image/png,AAA"
    assert saved["signed_off_at"]


async def test_user_clearing_a_signature_is_refused(patch_supabase):
    state = patch_supabase([_row(foreman_sign="data:image/png,AAA")])
    with pytest.raises(HTTPException) as exc:
        await update_work_order(1, WorkOrderUpdate(foreman_sign=""), _user())
    assert exc.value.status_code == 403
    assert state["work_orders"][0]["foreman_sign"] == "data:image/png,AAA"


async def test_user_resending_an_unchanged_foreman_name_is_allowed(patch_supabase):
    patch_supabase([_row(foreman_name="Lee Jones")])
    saved = await update_work_order(1, WorkOrderUpdate(foreman_name="Lee Jones", progress=10), _user())
    assert saved["progress"] == 10


# --- The bell's slices ---

async def test_overdue_slice_is_open_past_due_oldest_first(patch_supabase):
    import datetime as _dt
    from unittest import mock
    patch_supabase([
        _row(id=1, due_date="2026-10-20"),                       # overdue
        _row(id=2, due_date="2026-10-10", status="in-progress"),  # overdue, older
        _row(id=3, due_date="2026-10-30"),                       # not yet due
        _row(id=4, due_date=""),                                 # no due date: never overdue
        _row(id=5, due_date="2026-10-05", status="completed"),    # terminal: never overdue
        _row(id=6, due_date="2026-10-05", status="not-done"),     # terminal: never overdue
        _row(id=7, due_date="2026-10-12", status="postponed"),    # still open: overdue
    ])
    with mock.patch.object(m, "today_harare", return_value=_dt.date(2026, 10, 26)):
        got = await get_work_orders(overdue=True, current_user=_user())
    assert [r["id"] for r in got] == [2, 7, 1]


async def test_awaiting_signoff_slice_is_completed_and_unsigned(patch_supabase):
    patch_supabase([
        _row(id=1, status="completed", foreman_sign=""),
        _row(id=2, status="completed", foreman_sign="data:image/png,AAA"),
        _row(id=3, status="pending"),
    ])
    got = await get_work_orders(awaiting_signoff=True, current_user=_user())
    assert [r["id"] for r in got] == [1]


async def test_mine_slice_matches_the_session_person_only(patch_supabase):
    patch_supabase([
        _row(id=1, allocated_to="Alex Moyo"),                    # mine, exact
        _row(id=2, allocated_to="alex  moyo"),                  # mine, spacing/case
        _row(id=3, allocated_to="Lee Jones", artisan_name="Alex Moyo"),  # mine via artisan
        _row(id=4, allocated_to="Lee Jones"),                   # someone else's
        _row(id=5, allocated_to="Alex"),                        # looks like me, is not me
        _row(id=6, allocated_to="Alex Moyo", status="completed"),  # mine but done
    ])
    got = await get_work_orders(mine=True, current_user=_user())
    assert [r["id"] for r in got] == [1, 2, 3]


async def test_mine_slice_matches_email_when_the_name_differs(patch_supabase):
    patch_supabase([_row(id=1, allocated_to="alex@mine.test", artisan_name="")])
    got = await get_work_orders(mine=True, current_user=_user(name="Alexander Moyo"))
    assert [r["id"] for r in got] == [1]


async def test_mine_slice_is_never_served_from_another_users_cache(patch_supabase):
    # Cache keys vary by scope, not by caller: a cached mine slice would
    # serve one person's jobs to the next. The slice skips the cache.
    from app import cache as cache_mod
    cache_mod._reset_for_tests()
    patch_supabase([
        _row(id=1, allocated_to="Alex Moyo"),
        _row(id=2, allocated_to="Lee Jones"),
    ])
    alex = await get_work_orders(mine=True, current_user=_user())
    assert [r["id"] for r in alex] == [1]
    lee = await get_work_orders(mine=True, current_user=_user(name="Lee Jones", email="lee@mine.test"))
    assert [r["id"] for r in lee] == [2]


async def test_slices_compose_and_limit_applies_last(patch_supabase):
    import datetime as _dt
    from unittest import mock
    patch_supabase([
        _row(id=1, allocated_to="Alex Moyo", due_date="2026-10-20"),
        _row(id=2, allocated_to="Alex Moyo", due_date="2026-10-21"),
        _row(id=3, allocated_to="Lee Jones", due_date="2026-10-19"),
    ])
    with mock.patch.object(m, "today_harare", return_value=_dt.date(2026, 10, 26)):
        got = await get_work_orders(overdue=True, mine=True, limit=1, current_user=_user())
    assert [r["id"] for r in got] == [1]
