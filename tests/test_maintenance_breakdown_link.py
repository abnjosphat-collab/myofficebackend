# tests/test_maintenance_breakdown_link.py — M4: one entry for breakdowns.
#
# Logging a breakdown raises its work order (or skips it on request); a
# breakdown work order raises a breakdown shell for its downtime. The two are
# linked both ways through breakdowns.work_order_id (the M4 SQL file), and the
# code works before that file runs (jobs are still raised, shells are
# skipped, reverse reads answer None). Uses the shared breakdowns fake with
# auto-increment ids, since the link needs the created rows' ids back.
import pytest
from fastapi import HTTPException

import app.routers.breakdowns as bd
import app.routers.maintenance as wo
import app.breakdown_links as bl
from app.breakdown_links import (
    breakdown_shell_for_work_order,
    find_breakdown_for_work_order,
    work_order_values_for_breakdown,
)
from app.routers.breakdowns import BreakdownCreate, create_breakdown
from app.routers.maintenance import JobType, WorkOrderCreate, WorkOrderUpdate
from tests._breakdowns_fake import IdFakeSupabase as _IdFake

USER = {"user_id": "u1", "email": "t@t", "role": "user", "name": "T. Moyo"}
SCOPED = {"user_id": "u2", "email": "p@p", "role": "user", "name": "P. Ncube", "scope": "Projects"}


def _patch(monkeypatch, tables=None, link=True, wo_columns=True):
    """Both routers on one fake client; link probes pinned (never the real
    has_columns: it would cache YES against the fake for the whole suite)."""
    fake = _IdFake(tables or {
        "breakdowns": [], "work_orders": [], "lookup_lists": [],
        "maintenance_events": [], "equipment": [],
    })
    monkeypatch.setattr(bd, "supabase", fake)
    monkeypatch.setattr(wo, "supabase", fake)
    monkeypatch.setattr(bl, "has_columns", lambda *a, **k: link)
    monkeypatch.setattr(wo, "has_columns", lambda *a, **k: wo_columns)
    return fake


def _bd(**overrides) -> BreakdownCreate:
    base = dict(
        machine_id="M-1", machine_name="Crusher 1", artisan_name="T. Moyo",
        department="Milling", location="Bay 3", breakdown_date="2026-08-30",
        breakdown_type="mechanical", breakdown_description="Bearing seized",
    )
    base.update(overrides)
    return BreakdownCreate(**base)


def _wo(**overrides) -> WorkOrderCreate:
    base = dict(
        to_department="Engineering", to_section="Fitters", date_raised="2026-08-01",
        work_order_number="WO-CLIENT-GUESS", from_department="Mining", from_section="Ops",
        time_raised="08:00", account_number="ACC-1", equipment_info="Crusher 3",
        user_lab_today="Yes", job_type=JobType(maintenance=True),
        job_request_details="Bearing replacement needed", requested_by="J. Moyo",
        authorising_foreman="F. Ncube", authorising_engineer="E. Sibanda",
        allocated_to="T. Banda", estimated_hours="4", responsible_foreman="F. Ncube",
        job_instructions="Replace bearing on drive shaft",
        work_done_details="", cause_of_failure="", delay_details="",
        artisan_name="", artisan_sign="", artisan_date="",
        foreman_name="", foreman_sign="", foreman_date="",
        time_work_started="", time_work_finished="", total_time_worked="",
        overtime_start_time="", overtime_end_time="", overtime_hours="",
        delay_from_time="", delay_to_time="", total_delay_hours="",
    )
    base.update(overrides)
    return WorkOrderCreate(**base)


def _wo_inserts(fake):
    return [c for c in fake.state.calls if c["table"] == "work_orders" and c["op"] == "insert"]


def _bd_inserts(fake):
    return [c for c in fake.state.calls if c["table"] == "breakdowns" and c["op"] == "insert"]


# ─── The carried-over values (pure) ───────────────────────────────────────────

def test_values_carry_machine_artisan_department_date_and_reporter():
    values = work_order_values_for_breakdown(
        {"id": 5, "machine_name": "Crusher 1", "artisan_name": "T. Moyo",
         "department": "Milling", "breakdown_date": "2026-08-30",
         "breakdown_description": "Bearing seized", "priority": "high",
         "section": "Milling"},
        USER,
    )
    assert values["equipment_info"] == "Crusher 1"
    assert values["allocated_to"] == "T. Moyo"
    assert values["artisan_name"] == "T. Moyo"
    assert values["to_department"] == "Milling"
    assert values["classification"] == "breakdown"
    assert values["priority"] == "high"
    assert values["date_raised"] == "2026-08-30"
    assert values["job_request_details"] == "Bearing seized"
    assert values["requested_by"] == "T. Moyo"
    assert values["section"] == "Milling"
    assert "Crusher 1" in values["notes"] and "2026-08-30" in values["notes"]


def test_values_priority_critical_becomes_urgent_and_unknown_becomes_medium():
    assert work_order_values_for_breakdown({"priority": "critical"}, USER)["priority"] == "urgent"
    assert work_order_values_for_breakdown({"priority": "weird"}, USER)["priority"] == "medium"
    assert work_order_values_for_breakdown({}, USER)["priority"] == "medium"


def test_values_description_falls_back_before_the_attend_line():
    assert work_order_values_for_breakdown(
        {"machine_name": "Fan 2", "machine_description": "Rattling"}, USER,
    )["job_request_details"] == "Rattling"
    assert work_order_values_for_breakdown({"machine_name": "Fan 2"}, USER)[
        "job_request_details"] == "Attend breakdown on Fan 2"


def test_shell_maps_machine_artisan_priority_and_null_times():
    shell = breakdown_shell_for_work_order({
        "equipment_info": "Crusher 3", "allocated_to": "T. Banda",
        "priority": "urgent", "to_department": "Engineering",
        "date_raised": "2026-08-01", "job_request_details": "Bearing gone",
        "section": "Milling",
    })
    assert shell["machine_name"] == "Crusher 3"
    assert shell["machine_id"] == "Crusher 3"
    assert shell["artisan_name"] == "T. Banda"
    assert shell["priority"] == "critical"
    assert shell["status"] == "logged"
    assert shell["breakdown_type"] == "other"
    assert shell["breakdown_date"] == "2026-08-01"
    assert shell["breakdown_description"] == "Bearing gone"
    assert shell["section"] == "Milling"
    assert shell["downtime_minutes"] is None
    assert shell["response_time_minutes"] is None
    assert shell["repair_time_minutes"] is None
    assert shell["net_downtime_minutes"] is None
    assert shell["breakdown_start"] is None


def test_shell_date_defaults_to_today_when_the_job_has_none():
    from app.clock import today_harare
    shell = breakdown_shell_for_work_order({"equipment_info": "Fan 2", "date_raised": ""})
    assert shell["breakdown_date"] == today_harare().isoformat()


# ─── Breakdown → work order ───────────────────────────────────────────────────

async def test_create_breakdown_raises_and_links_its_work_order_by_default(monkeypatch):
    fake = _patch(monkeypatch)
    result = await create_breakdown(_bd(), current_user=USER)

    wo_payload = _wo_inserts(fake)[0]["payload"]
    assert wo_payload["classification"] == "breakdown"
    assert wo_payload["equipment_info"] == "Crusher 1"
    assert wo_payload["job_request_details"] == "Bearing seized"
    assert wo_payload["work_order_number"].startswith("WO-")

    bd_payload = _bd_inserts(fake)[0]["payload"]
    assert bd_payload["work_order_id"] == 1
    assert result["data"]["work_order_id"] == 1
    assert result["work_order"]["id"] == 1
    assert result["work_order"]["work_order_number"] == wo_payload["work_order_number"]


async def test_create_breakdown_opt_out_raises_no_work_order(monkeypatch):
    fake = _patch(monkeypatch)
    result = await create_breakdown(_bd(raise_work_order=False), current_user=USER)
    assert _wo_inserts(fake) == []
    assert "work_order_id" not in _bd_inserts(fake)[0]["payload"]
    assert result["work_order"] is None


async def test_create_breakdown_before_the_m4_sql_raises_but_stores_no_link(monkeypatch):
    fake = _patch(monkeypatch, link=False)
    result = await create_breakdown(_bd(), current_user=USER)
    # The job is real and useful on its own, so it is still raised; only the
    # link waits for the column.
    assert len(_wo_inserts(fake)) == 1
    assert "work_order_id" not in _bd_inserts(fake)[0]["payload"]
    assert result["work_order"]["work_order_number"].startswith("WO-")
    assert result["data"].get("work_order_id") is None


async def test_raise_work_order_for_existing_breakdown_links_and_answers_again(monkeypatch):
    from app.routers.breakdowns import raise_work_order_for_breakdown
    fake = _patch(monkeypatch, tables={
        "breakdowns": [{"id": 9, "machine_name": "Fan 2", "machine_id": "F2",
                        "artisan_name": "A. B", "department": "Milling", "location": "Bay",
                        "breakdown_date": "2026-08-30", "breakdown_type": "mechanical",
                        "breakdown_description": "Rattling", "section": None}],
        "work_orders": [], "lookup_lists": [], "maintenance_events": [], "equipment": [],
    })
    first = await raise_work_order_for_breakdown(9, current_user=USER)
    assert first["already_linked"] is False
    assert first["work_order"]["work_order_number"].startswith("WO-")
    assert _wo_inserts(fake)[0]["payload"]["classification"] == "breakdown"
    assert fake.state.tables["breakdowns"][0]["work_order_id"] == first["work_order"]["id"]

    second = await raise_work_order_for_breakdown(9, current_user=USER)
    assert second["already_linked"] is True
    assert second["work_order"]["id"] == first["work_order"]["id"]
    assert len(_wo_inserts(fake)) == 1  # the second call raises nothing


async def test_raise_work_order_for_unknown_breakdown_is_404(monkeypatch):
    from app.routers.breakdowns import raise_work_order_for_breakdown
    _patch(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await raise_work_order_for_breakdown(12345, current_user=USER)
    assert exc.value.status_code == 404


async def test_scoped_user_cannot_raise_for_an_out_of_scope_breakdown(monkeypatch):
    from app.routers.breakdowns import raise_work_order_for_breakdown
    fake = _patch(monkeypatch, tables={
        "breakdowns": [{"id": 9, "machine_name": "Fan 2", "machine_id": "F2",
                        "artisan_name": "A. B", "department": "Milling", "location": "Bay",
                        "breakdown_date": "2026-08-30", "breakdown_type": "mechanical",
                        "breakdown_description": "Rattling", "section": "Milling"}],
        "work_orders": [], "lookup_lists": [], "maintenance_events": [], "equipment": [],
    })
    with pytest.raises(HTTPException) as exc:
        await raise_work_order_for_breakdown(9, current_user=SCOPED)
    assert exc.value.status_code == 404
    assert _wo_inserts(fake) == []


async def test_scoped_raise_stamps_both_rows_to_the_scope(monkeypatch):
    fake = _patch(monkeypatch)
    await create_breakdown(_bd(), current_user=SCOPED)
    assert _bd_inserts(fake)[0]["payload"]["section"] == "Projects"
    assert _wo_inserts(fake)[0]["payload"]["section"] == "Projects"


# ─── Work order → breakdown ───────────────────────────────────────────────────

async def test_create_breakdown_work_order_raises_a_linked_shell(monkeypatch):
    fake = _patch(monkeypatch)
    result = await wo.create_work_order(_wo(classification="breakdown"), current_user=USER)

    shell = _bd_inserts(fake)[0]["payload"]
    assert shell["machine_name"] == "Crusher 3"
    assert shell["status"] == "logged"
    assert shell["downtime_minutes"] is None
    assert shell["work_order_id"] == result["id"]
    assert result["breakdown_id"] == shell["id"]


async def test_create_planned_work_order_raises_no_shell(monkeypatch):
    fake = _patch(monkeypatch)
    result = await wo.create_work_order(_wo(classification="planned_maintenance"), current_user=USER)
    assert _bd_inserts(fake) == []
    assert "breakdown_id" not in result


async def test_create_breakdown_work_order_before_the_m4_sql_skips_the_shell(monkeypatch):
    fake = _patch(monkeypatch, link=False)
    result = await wo.create_work_order(_wo(classification="breakdown"), current_user=USER)
    # A shell with no link is the double entry M4 removes, so none is raised.
    assert _bd_inserts(fake) == []
    assert result["breakdown_id"] is None


async def test_classifying_a_work_order_as_breakdown_raises_the_shell(monkeypatch):
    fake = _patch(monkeypatch, tables={
        "breakdowns": [],
        "work_orders": [{"id": 4, "work_order_number": "WO-00004", "equipment_info": "Fan 2",
                         "status": "pending", "classification": None, "section": None,
                         "allocated_to": "T. Banda", "to_department": "Engineering",
                         "date_raised": "2026-08-01", "job_request_details": "Rattling",
                         "priority": "high", "updated_at": "2026-08-01T00:00:00"}],
        "lookup_lists": [], "maintenance_events": [], "equipment": [],
    })
    result = await wo.update_work_order(4, WorkOrderUpdate(classification="breakdown"), current_user=USER)
    shell = _bd_inserts(fake)[0]["payload"]
    assert shell["machine_name"] == "Fan 2"
    assert shell["work_order_id"] == 4
    assert result["breakdown_id"] == shell["id"]


async def test_record_breakdown_refuses_a_job_that_is_not_a_breakdown(monkeypatch):
    from app.routers.maintenance import record_breakdown_for_work_order
    _patch(monkeypatch, tables={
        "breakdowns": [],
        "work_orders": [{"id": 4, "work_order_number": "WO-00004", "classification": "planned_maintenance",
                         "section": None, "status": "pending"}],
        "lookup_lists": [], "maintenance_events": [], "equipment": [],
    })
    with pytest.raises(HTTPException) as exc:
        await record_breakdown_for_work_order(4, current_user=USER)
    assert exc.value.status_code == 422


async def test_record_breakdown_is_idempotent_and_answers_the_link(monkeypatch):
    from app.routers.maintenance import get_work_order_breakdown, record_breakdown_for_work_order
    fake = _patch(monkeypatch, tables={
        "breakdowns": [],
        "work_orders": [{"id": 4, "work_order_number": "WO-00004", "classification": "breakdown",
                         "section": None, "status": "pending", "equipment_info": "Fan 2",
                         "allocated_to": "T. Banda", "to_department": "Engineering",
                         "date_raised": "2026-08-01", "job_request_details": "Rattling",
                         "priority": "high"}],
        "lookup_lists": [], "maintenance_events": [], "equipment": [],
    })
    assert await get_work_order_breakdown(4, current_user=USER) is None

    first = await record_breakdown_for_work_order(4, current_user=USER)
    assert first["already_linked"] is False
    assert first["breakdown"]["work_order_id"] == 4

    second = await record_breakdown_for_work_order(4, current_user=USER)
    assert second["already_linked"] is True
    assert second["breakdown"]["id"] == first["breakdown"]["id"]
    assert len(_bd_inserts(fake)) == 1

    assert (await get_work_order_breakdown(4, current_user=USER))["id"] == first["breakdown"]["id"]


async def test_record_breakdown_before_the_m4_sql_says_so_plainly(monkeypatch):
    from app.routers.maintenance import record_breakdown_for_work_order
    _patch(monkeypatch, link=False, tables={
        "breakdowns": [],
        "work_orders": [{"id": 4, "work_order_number": "WO-00004", "classification": "breakdown",
                         "section": None, "status": "pending", "equipment_info": "Fan 2"}],
        "lookup_lists": [], "maintenance_events": [], "equipment": [],
    })
    with pytest.raises(HTTPException) as exc:
        await record_breakdown_for_work_order(4, current_user=USER)
    assert exc.value.status_code == 409


async def test_reverse_read_before_the_m4_sql_is_honestly_empty(monkeypatch):
    from app.routers.maintenance import get_work_order_breakdown
    _patch(monkeypatch, link=False, tables={
        "breakdowns": [{"id": 1, "work_order_id": 4}],
        "work_orders": [{"id": 4, "work_order_number": "WO-00004", "classification": "breakdown",
                         "section": None, "status": "pending"}],
        "lookup_lists": [], "maintenance_events": [], "equipment": [],
    })
    assert await get_work_order_breakdown(4, current_user=USER) is None


def test_find_breakdown_for_work_order_answers_none_before_the_sql(monkeypatch):
    monkeypatch.setattr(bl, "has_columns", lambda *a, **k: False)
    assert find_breakdown_for_work_order(_IdFake({"breakdowns": []}), 4) is None


# ─── Deleting ─────────────────────────────────────────────────────────────────

async def test_scoped_reverse_read_hides_an_out_of_scope_shell(monkeypatch):
    from app.routers.maintenance import get_work_order_breakdown, record_breakdown_for_work_order
    _patch(monkeypatch, tables={
        "breakdowns": [{"id": 7, "work_order_id": 4, "section": "Milling",
                        "machine_name": "Fan 2", "spares_used": "[]"}],
        "work_orders": [{"id": 4, "work_order_number": "WO-00004", "classification": "breakdown",
                         "section": "Projects", "status": "pending"}],
        "lookup_lists": [], "maintenance_events": [], "equipment": [],
    })
    # The job is in scope, its shell was moved out: the read is empty, and
    # recording refuses instead of raising a second shell.
    assert await get_work_order_breakdown(4, current_user=SCOPED) is None
    with pytest.raises(HTTPException) as exc:
        await record_breakdown_for_work_order(4, current_user=SCOPED)
    assert exc.value.status_code == 404


async def test_scoped_reverse_read_sees_an_in_scope_shell(monkeypatch):
    from app.routers.maintenance import get_work_order_breakdown
    _patch(monkeypatch, tables={
        "breakdowns": [{"id": 7, "work_order_id": 4, "section": "Projects",
                        "machine_name": "Fan 2", "spares_used": "[]"}],
        "work_orders": [{"id": 4, "work_order_number": "WO-00004", "classification": "breakdown",
                         "section": "Projects", "status": "pending"}],
        "lookup_lists": [], "maintenance_events": [], "equipment": [],
    })
    found = await get_work_order_breakdown(4, current_user=SCOPED)
    assert found is not None and found["id"] == 7


async def test_delete_breakdown_with_a_work_order_is_refused_by_number(monkeypatch):
    _patch(monkeypatch, tables={
        "breakdowns": [{"id": 9, "machine_name": "Fan 2", "work_order_id": 4, "section": None}],
        "work_orders": [{"id": 4, "work_order_number": "WO-00004", "section": None}],
        "lookup_lists": [], "maintenance_events": [], "equipment": [],
    })
    with pytest.raises(HTTPException) as exc:
        await bd.delete_breakdown(9, current_user={**USER, "role": "manager"})
    assert exc.value.status_code == 409
    assert "WO-00004" in str(exc.value.detail)
