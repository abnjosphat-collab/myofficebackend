# tests/test_maintenance_registers.py — slice 3 of the Maintenance rebuild: the leave rule, the Tools
# register bridge and the tools list on a work order. Same recipe as the other maintenance tests: call the
# route coroutines directly against an in-memory fake supabase.

from datetime import date

import pytest
from fastapi import HTTPException

import app.maintenance_registers as reg
import app.routers.maintenance as m
from app.routers.maintenance import (
    WorkOrderToolIn, WorkOrderToolsReplace, WorkOrderUpdate,
    create_work_order, get_people_on_leave, get_tools_register, get_work_order_tools,
    replace_work_order_tools, update_work_order,
)
from tests.test_maintenance_work_orders import (  # noqa: F401  (fixtures and builders are shared)
    _FakeSupabase, _no_redis, _user, _wo, patch_supabase,
)

TODAY = date(2026, 8, 10)


def _leave(name="T. Banda", start="2026-08-05", end="2026-08-14", status="approved", kind="Annual leave"):
    return {"employee_id": "E1", "employee_name": name, "leave_type": kind,
            "start_date": start, "end_date": end, "status": status}


def _db(**tables):
    return _FakeSupabase(dict(tables))


# ─── people_on_leave ────────────────────────────────────────────────────────────────

def test_people_on_leave_lists_only_approved_leave_covering_the_day():
    db = _db(leaves=[
        _leave("A. Away"),
        _leave("P. Pending", status="pending"),
        _leave("L. Later", start="2026-08-11", end="2026-08-12"),
        _leave("E. Earlier", start="2026-08-01", end="2026-08-09"),
        _leave("B. Boundary", start="2026-08-10", end="2026-08-10"),
    ])
    out = reg.people_on_leave(db, TODAY)
    assert [p["employee_name"] for p in out] == ["A. Away", "B. Boundary"]
    assert out[0]["end_date"] == "2026-08-14" and out[0]["leave_type"] == "Annual leave"


def test_people_on_leave_read_failure_is_a_503_not_an_empty_list():
    class Boom:
        def table(self, name): raise ConnectionError("down")

    with pytest.raises(HTTPException) as err:
        reg.people_on_leave(Boom(), TODAY)
    assert err.value.status_code == 503


# ─── refuse_people_on_leave ─────────────────────────────────────────────────────────

def test_refuse_names_the_person_and_the_return_date():
    db = _db(leaves=[_leave("T. Banda")])
    with pytest.raises(HTTPException) as err:
        reg.refuse_people_on_leave(db, {"allocated_to": "T. Banda"}, on=TODAY)
    assert err.value.status_code == 409
    detail = err.value.detail
    assert detail["code"] == "person_on_leave" and "2026-08-14" in detail["message"]
    assert detail["people"][0]["field"] == "allocated_to"


def test_refuse_matches_a_typed_name_ignoring_case_and_spacing():
    db = _db(leaves=[_leave("T. Banda")])
    with pytest.raises(HTTPException):
        reg.refuse_people_on_leave(db, {"artisan_name": "  t.   BANDA "}, on=TODAY)


def test_refuse_allows_people_not_on_leave_and_blank_fields():
    db = _db(leaves=[_leave("Someone Else")])
    reg.refuse_people_on_leave(db, {"allocated_to": "T. Banda", "artisan_name": ""}, on=TODAY)


def test_refuse_does_not_read_leave_when_no_person_is_named():
    class Boom:
        def table(self, name): raise AssertionError("must not read")

    reg.refuse_people_on_leave(Boom(), {"allocated_to": "", "artisan_name": None}, on=TODAY)


def test_refuse_ignores_the_requester():
    db = _db(leaves=[_leave("J. Moyo")])
    reg.refuse_people_on_leave(db, {"requested_by": "J. Moyo"}, on=TODAY)


# ─── the rule on create and update ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_refuses_someone_on_leave_and_stores_nothing(patch_supabase, monkeypatch):
    state = patch_supabase([])
    state["leaves"] = [_leave("T. Banda", start="2000-01-01", end="2999-01-01")]
    with pytest.raises(HTTPException) as err:
        await create_work_order(_wo(allocated_to="T. Banda"), _user())
    assert err.value.status_code == 409 and err.value.detail["code"] == "person_on_leave"
    assert state["work_orders"] == []


@pytest.mark.asyncio
async def test_update_refuses_a_newly_named_person_on_leave(patch_supabase):
    state = patch_supabase([{"id": 1, "work_order_number": "WO-1", "allocated_to": "F. Ncube"}])
    state["leaves"] = [_leave("T. Banda", start="2000-01-01", end="2999-01-01")]
    with pytest.raises(HTTPException) as err:
        await update_work_order(1, WorkOrderUpdate(allocated_to="T. Banda"), _user())
    assert err.value.status_code == 409
    assert state["work_orders"][0]["allocated_to"] == "F. Ncube"


@pytest.mark.asyncio
async def test_update_leaves_an_unchanged_name_alone_so_old_records_still_save(patch_supabase):
    state = patch_supabase([{"id": 1, "work_order_number": "WO-1", "allocated_to": "T. Banda", "title": "a"}])
    state["leaves"] = [_leave("T. Banda", start="2000-01-01", end="2999-01-01")]
    out = await update_work_order(1, WorkOrderUpdate(allocated_to="T. Banda", title="b"), _user())
    assert out["title"] == "b"


@pytest.mark.asyncio
async def test_leave_endpoint_returns_the_list(patch_supabase):
    state = patch_supabase([])
    state["leaves"] = [_leave("A. Away")]
    out = await get_people_on_leave(TODAY)
    assert [p["employee_name"] for p in out] == ["A. Away"]


# ─── Tools register bridge ──────────────────────────────────────────────────────────

def _tool(**o):
    base = {"id": "t1", "register_number": "PP-UG-0001", "name": "Torque wrench", "make_model": "Gedore",
            "category": "Hand tools", "equipment_kind": "hand-tool", "department": "Engineering",
            "status": "available", "archived": False, "custody": None, "condition": "Good",
            "weekly_inspection_required": False, "monthly_inspection_required": False,
            "quarterly_inspection_required": False, "calibration_required": False}
    base.update(o)
    return base


def test_tools_feed_leaves_out_archived_and_returns_only_picker_fields():
    db = _db(tools_workspace_equipment=[_tool(), _tool(id="t2", register_number="PP-UG-0002", archived=True)],
             tools_workspace_inspections=[])
    out = reg.tools_feed(db)
    assert [t["register_number"] for t in out] == ["PP-UG-0001"]
    assert set(out[0]) == {"id", "register_number", "name", "make_model", "category", "equipment_kind",
                           "department", "status", "holder", "expected_return_at", "inspection_due", "condition"}


def test_tools_feed_derives_overdue_from_custody_like_the_tools_workspace():
    custody = {"employee_name": "T. Banda", "expected_return_at": "2020-01-01T00:00:00+00:00"}
    db = _db(tools_workspace_equipment=[_tool(status="issued", custody=custody)], tools_workspace_inspections=[])
    out = reg.tools_feed(db)[0]
    assert out["status"] == "overdue" and out["holder"] == "T. Banda"
    assert out["expected_return_at"] == "2020-01-01T00:00:00+00:00"


def test_tools_feed_lists_the_checks_that_are_due():
    db = _db(tools_workspace_equipment=[_tool(monthly_inspection_required=True)], tools_workspace_inspections=[])
    assert reg.tools_feed(db)[0]["inspection_due"] == ["monthly"]


def test_tools_feed_read_failure_is_a_503_not_an_empty_list():
    class Boom:
        def table(self, name): raise ConnectionError("down")

    with pytest.raises(HTTPException) as err:
        reg.tools_feed(Boom())
    assert err.value.status_code == 503


def test_the_bridge_exposes_no_write_route():
    bridge = [r for r in m.router.routes if getattr(r, "path", "").startswith("/registers/")]
    assert bridge and all(r.methods == {"GET"} for r in bridge)


# ─── tools on a work order ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_replaces_the_list_and_audits_the_change(patch_supabase):
    state = patch_supabase([{"id": 1, "work_order_number": "WO-1"}])
    state["work_order_tools"] = [{"id": 90, "work_order_id": 1, "tool_register_number": "OLD", "tool_name": "Old tool"}]
    body = WorkOrderToolsReplace(tools=[
        WorkOrderToolIn(tool_register_number="PP-UG-0001", tool_name="Torque wrench", note="24 mm"),
        WorkOrderToolIn(tool_register_number="PP-UG-0001", tool_name="Torque wrench"),
        WorkOrderToolIn(tool_name="Big hammer"),
    ])
    out = await replace_work_order_tools(1, body, _user())
    assert [t["tool_name"] for t in out] == ["Torque wrench", "Big hammer"]
    assert out[0]["note"] == "24 mm" and out[0]["added_by"] == "u-1" and out[1]["tool_register_number"] is None
    assert [t["tool_name"] for t in state["work_order_tools"]] == ["Torque wrench", "Big hammer"]
    event = state["maintenance_events"][0]
    assert event["changes"] == {"tools": [["OLD"], ["PP-UG-0001", "Big hammer"]]}


@pytest.mark.asyncio
async def test_put_with_the_same_tools_writes_no_audit_event(patch_supabase):
    state = patch_supabase([{"id": 1, "work_order_number": "WO-1"}])
    state["work_order_tools"] = [{"id": 90, "work_order_id": 1, "tool_register_number": "A", "tool_name": "A"}]
    await replace_work_order_tools(1, WorkOrderToolsReplace(tools=[WorkOrderToolIn(tool_register_number="A", tool_name="A")]), _user())
    assert not state.get("maintenance_events")


@pytest.mark.asyncio
async def test_put_unknown_work_order_is_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as err:
        await replace_work_order_tools(9, WorkOrderToolsReplace(tools=[]), _user())
    assert err.value.status_code == 404


@pytest.mark.asyncio
async def test_a_failed_save_restores_the_previous_list(patch_supabase, monkeypatch):
    state = patch_supabase([{"id": 1, "work_order_number": "WO-1"}])
    state["work_order_tools"] = [{"id": 90, "work_order_id": 1, "tool_register_number": "A", "tool_name": "Keep me"}]
    real = _FakeSupabase(state)

    class Flaky:
        inserts = 0

        def table(self, name):
            q = real.table(name)
            if name == "work_order_tools":
                original = q.insert

                def insert(data):
                    Flaky.inserts += 1
                    if Flaky.inserts == 1:
                        raise ConnectionError("db down")
                    return original(data)
                q.insert = insert
            return q

    monkeypatch.setattr(m, "supabase", Flaky())
    with pytest.raises(HTTPException) as err:
        await replace_work_order_tools(1, WorkOrderToolsReplace(tools=[WorkOrderToolIn(tool_name="New")]), _user())
    assert err.value.status_code == 500 and "previous list was kept" in err.value.detail
    assert [t["tool_name"] for t in state["work_order_tools"]] == ["Keep me"]


@pytest.mark.asyncio
async def test_get_tools_lists_in_order_and_404s_for_unknown(patch_supabase):
    state = patch_supabase([{"id": 1}])
    state["work_order_tools"] = [
        {"id": 2, "work_order_id": 1, "tool_name": "B"}, {"id": 1, "work_order_id": 1, "tool_name": "A"},
        {"id": 3, "work_order_id": 2, "tool_name": "Other job"},
    ]
    out = await get_work_order_tools(1)
    assert [t["tool_name"] for t in out] == ["A", "B"]
    with pytest.raises(HTTPException) as err:
        await get_work_order_tools(5)
    assert err.value.status_code == 404


def test_a_blank_tool_name_is_rejected_by_the_model():
    with pytest.raises(ValueError):
        WorkOrderToolIn(tool_name="   ")
    with pytest.raises(ValueError):
        WorkOrderToolsReplace(tools=[WorkOrderToolIn(tool_name="x")] * 51)


@pytest.mark.asyncio
async def test_get_tools_register_route_returns_the_feed(monkeypatch):
    db = _db(tools_workspace_equipment=[_tool()], tools_workspace_inspections=[])
    monkeypatch.setattr(m, "supabase", db)
    assert [t["register_number"] for t in await get_tools_register()] == ["PP-UG-0001"]
