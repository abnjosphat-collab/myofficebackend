# tests/test_maintenance_audit.py — slice 1 of the Maintenance rebuild: the audit trail, the row-version
# check on PATCH, and work-order comments. Same recipe as test_maintenance_work_orders.py: call the route
# coroutines directly against an in-memory fake supabase.

import pytest
from fastapi import HTTPException

import app.maintenance_events as ev
from app.routers.maintenance import (
    WorkOrderCommentCreate, WorkOrderUpdate,
    add_work_order_comment, create_work_order, delete_work_order,
    get_work_order_comments, get_work_order_events, update_work_order,
)
from tests.test_maintenance_work_orders import (  # noqa: F401  (fixtures and builders are shared)
    _FakeSupabase, _manager, _no_redis, _user, _wo, patch_supabase,
)


# ─── diff_changes ───────────────────────────────────────────────────────────────────

def test_diff_changes_reports_old_and_new_only_for_changed_fields():
    out = ev.diff_changes({"status": "pending", "priority": "low"}, {"status": "completed", "priority": "low"})
    assert out == {"status": ["pending", "completed"]}


def test_diff_changes_skips_bookkeeping_columns():
    out = ev.diff_changes({"updated_at": "a", "version": 1}, {"updated_at": "b", "version": 2})
    assert out == {}


def test_diff_changes_redacts_signature_images():
    out = ev.diff_changes({"artisan_sign": ""}, {"artisan_sign": "data:image/png;base64,AAAA"})
    assert out == {"artisan_sign": [None, "(signature)"]}


def test_diff_changes_shortens_very_long_text():
    out = ev.diff_changes({"notes": ""}, {"notes": "x" * 2000})
    assert out["notes"][1].endswith("…") and len(out["notes"][1]) == 501


def test_diff_changes_limits_to_the_given_fields():
    out = ev.diff_changes({"a": 1, "b": 1}, {"a": 2, "b": 2}, fields=["a"])
    assert out == {"a": [1, 2]}


# ─── append_event ───────────────────────────────────────────────────────────────────

def test_append_event_stores_actor_and_returns_true(patch_supabase):
    state = patch_supabase([])
    ok = ev.append_event(
        _FakeSupabase(state), entity="work_order", entity_id=5, action="updated", user=_user(),
        entity_number="WO-00005", changes={"status": ["a", "b"]},
    )
    assert ok is True
    row = state["maintenance_events"][0]
    assert row["entity_id"] == 5 and row["actor_user_id"] == "u-1" and row["actor_name"] == "u@x.com"


def test_append_event_never_raises_and_says_it_failed():
    class Boom:
        def table(self, name):
            raise RuntimeError("relation does not exist")
    assert ev.append_event(Boom(), entity="work_order", entity_id=1, action="x", user=_user()) is False


# ─── PATCH: version check ───────────────────────────────────────────────────────────

async def test_update_without_version_still_works_and_is_audited(patch_supabase):
    state = patch_supabase([{"id": 7, "status": "pending", "work_order_number": "WO-00007", "version": 3}])
    result = await update_work_order(7, WorkOrderUpdate(status="completed"), current_user=_user())
    assert result["status"] == "completed"
    event = state["maintenance_events"][0]
    assert event["action"] == "updated" and event["changes"] == {"status": ["pending", "completed"]}
    assert event["from_status"] == "pending" and event["to_status"] == "completed"
    assert event["entity_number"] == "WO-00007"


async def test_update_with_current_version_succeeds(patch_supabase):
    state = patch_supabase([{"id": 7, "status": "pending", "version": 3}])
    result = await update_work_order(7, WorkOrderUpdate(status="completed", version=3), current_user=_user())
    assert result["status"] == "completed"
    payload = [c for c in state["calls"] if c["mode"] == "update"][0]["payload"]
    assert "version" not in payload  # a precondition, never a stored value
    assert ("version", 3) in [c for c in state["calls"] if c["mode"] == "update"][0]["filters"]


async def test_update_with_stale_version_is_409_and_returns_the_current_row(patch_supabase):
    state = patch_supabase([{"id": 7, "status": "in_progress", "version": 5}])
    with pytest.raises(HTTPException) as exc:
        await update_work_order(7, WorkOrderUpdate(status="completed", version=4), current_user=_user())
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "version_conflict"
    assert exc.value.detail["current"]["status"] == "in_progress"
    assert state["work_orders"][0]["status"] == "in_progress"  # nothing written
    assert "maintenance_events" not in state  # and nothing audited


async def test_update_version_is_ignored_when_the_column_does_not_exist_yet(patch_supabase):
    patch_supabase([{"id": 7, "status": "pending"}])  # migration not applied: no version on the row
    result = await update_work_order(7, WorkOrderUpdate(status="completed", version=1), current_user=_user())
    assert result["status"] == "completed"


async def test_update_that_changes_nothing_writes_no_audit_row(patch_supabase):
    state = patch_supabase([{"id": 7, "status": "pending", "version": 1}])
    await update_work_order(7, WorkOrderUpdate(status="pending"), current_user=_user())
    assert "maintenance_events" not in state


async def test_update_succeeds_even_if_the_audit_append_fails(patch_supabase, monkeypatch):
    patch_supabase([{"id": 7, "status": "pending", "version": 1}])
    import app.routers.maintenance as m
    monkeypatch.setattr(m, "append_event", lambda *a, **k: False)
    result = await update_work_order(7, WorkOrderUpdate(status="completed"), current_user=_user())
    assert result["status"] == "completed"


# ─── create and delete are audited ──────────────────────────────────────────────────

async def test_create_writes_a_created_event(patch_supabase):
    state = patch_supabase([])
    created = await create_work_order(_wo(), current_user=_user())
    event = state["maintenance_events"][0]
    assert event["action"] == "created" and event["entity_id"] == created["id"]
    assert event["entity_number"] == created["work_order_number"]


async def test_delete_writes_a_deleted_event_that_keeps_the_number(patch_supabase):
    state = patch_supabase([{"id": 3, "status": "pending", "work_order_number": "WO-00003"}])
    await delete_work_order(3, current_user=_manager())
    event = state["maintenance_events"][0]
    assert event["action"] == "deleted" and event["entity_number"] == "WO-00003"


# ─── events and comments endpoints ──────────────────────────────────────────────────

async def test_events_are_returned_for_that_work_order_newest_first(patch_supabase):
    state = patch_supabase([])
    state["maintenance_events"] = [
        {"id": 1, "entity": "work_order", "entity_id": 7, "action": "created", "created_at": "2026-10-01T08:00:00"},
        {"id": 2, "entity": "work_order", "entity_id": 7, "action": "updated", "created_at": "2026-10-02T08:00:00"},
        {"id": 3, "entity": "work_order", "entity_id": 8, "action": "created", "created_at": "2026-10-03T08:00:00"},
        {"id": 4, "entity": "request", "entity_id": 7, "action": "created", "created_at": "2026-10-04T08:00:00"},
    ]
    result = await get_work_order_events(7)
    assert [e["id"] for e in result] == [2, 1]


async def test_events_failure_is_an_error_not_an_empty_list(monkeypatch):
    import app.routers.maintenance as m

    class Boom:
        def table(self, name):
            raise RuntimeError("relation does not exist")
    monkeypatch.setattr(m, "supabase", Boom())
    with pytest.raises(HTTPException) as exc:
        await get_work_order_events(7)
    assert exc.value.status_code == 500


async def test_comments_come_back_oldest_first(patch_supabase):
    state = patch_supabase([])
    state["work_order_comments"] = [
        {"id": 2, "work_order_id": 7, "body": "second", "created_at": "2026-10-02T08:00:00"},
        {"id": 1, "work_order_id": 7, "body": "first", "created_at": "2026-10-01T08:00:00"},
        {"id": 3, "work_order_id": 9, "body": "other", "created_at": "2026-10-01T09:00:00"},
    ]
    result = await get_work_order_comments(7)
    assert [c["body"] for c in result] == ["first", "second"]


async def test_comments_failure_is_an_error_not_an_empty_list(monkeypatch):
    import app.routers.maintenance as m

    class Boom:
        def table(self, name):
            raise RuntimeError("down")
    monkeypatch.setattr(m, "supabase", Boom())
    with pytest.raises(HTTPException) as exc:
        await get_work_order_comments(7)
    assert exc.value.status_code == 500


async def test_add_comment_stores_author_and_audits(patch_supabase):
    state = patch_supabase([{"id": 7, "status": "pending", "work_order_number": "WO-00007"}])
    created = await add_work_order_comment(7, WorkOrderCommentCreate(body="  Bearing ordered  "), current_user=_user())
    assert created["body"] == "Bearing ordered"
    assert created["author_name"] == "u@x.com" and created["author_user_id"] == "u-1"
    assert state["maintenance_events"][0]["action"] == "commented"


async def test_add_comment_to_a_missing_work_order_is_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await add_work_order_comment(404, WorkOrderCommentCreate(body="hi"), current_user=_user())
    assert exc.value.status_code == 404


def test_a_blank_comment_is_rejected():
    with pytest.raises(ValueError):
        WorkOrderCommentCreate(body="    ")
