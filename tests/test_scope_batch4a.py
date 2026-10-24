# tests/test_scope_batch4a.py — Batch 4 enforcement: equipment, work orders,
# breakdowns, notices, SOPs, services. New section columns are NULL until
# triaged, and NULL means "not Projects": scoped viewers list their slice,
# read or write anything else as 404 (writing nothing), creates land
# stamped, and stats/dashboards see the slice only.

import pytest

from app.scope import apply_list_scope
from tests.scope_harness import (
    SCOPED, SCOPED_MGR, UNSCOPED, mount_router,
)


def _predicates(client):
    return [c for c in client.scope_calls if c[0] in ("eq", "in")]


def test_apply_list_scope_with_anonymous_user_is_passthrough():
    assert apply_list_scope("q", None, "work_orders") == "q"


# ─── equipment ───────────────────────────────────────────────────────────────

def _eq_store():
    return {"equipment": [
        {"id": 1, "name": "Mill 1", "equipment_id": "M-1", "section": "Projects",
         "status": "operational"},
        {"id": 2, "name": "Mill 2", "equipment_id": "M-2", "section": "Mechanical",
         "status": "operational"},
        {"id": 3, "name": "Pump 9", "equipment_id": "P-9", "section": None,
         "status": "operational"},
    ]}


@pytest.fixture
def eq_client(monkeypatch):
    import app.routers.equipment as mod
    return mount_router(monkeypatch, mod, "/api/equipment", _eq_store())


def test_equipment_scoped_list_is_predicated(eq_client):
    eq_client.as_user(SCOPED)
    resp = eq_client.get("/api/equipment")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]
    assert ("eq", "section", "Projects") in _predicates(eq_client)


def test_equipment_unscoped_list_includes_null_rows(eq_client):
    eq_client.as_user(UNSCOPED)
    assert [r["id"] for r in eq_client.get("/api/equipment").json()] == [1, 2, 3]
    assert _predicates(eq_client) == []


def test_equipment_scoped_single_of_other_section_is_404(eq_client):
    eq_client.as_user(SCOPED)
    assert eq_client.get("/api/equipment/2").status_code == 404
    assert eq_client.get("/api/equipment/3").status_code == 404
    assert eq_client.get("/api/equipment/1").status_code == 200


def test_equipment_scoped_create_is_stamped_projects(eq_client):
    eq_client.as_user(SCOPED)
    resp = eq_client.post("/api/equipment", json={"name": "Mill 3", "equipment_id": "M-3"})
    assert resp.status_code == 200, resp.text
    assert eq_client.scope_store["equipment"][-1]["section"] == "Projects"


def test_equipment_scoped_move_out_writes_nothing(eq_client):
    eq_client.as_user(SCOPED)
    resp = eq_client.put("/api/equipment/1", json={"name": "Mill 1", "equipment_id": "M-1", "section": "Mechanical"})
    assert resp.status_code == 404
    assert eq_client.scope_store["equipment"][0]["section"] == "Projects"


def test_equipment_scoped_patch_inside_section_works(eq_client):
    eq_client.as_user(SCOPED)
    resp = eq_client.put("/api/equipment/1", json={"name": "Mill 1", "equipment_id": "M-1", "location": "Decline"})
    assert resp.status_code == 200
    assert eq_client.scope_store["equipment"][0]["location"] == "Decline"


def test_equipment_scoped_manager_cannot_delete_other_section(eq_client):
    eq_client.as_user(SCOPED_MGR)
    assert eq_client.delete("/api/equipment/2").status_code == 404
    assert len(eq_client.scope_store["equipment"]) == 3


# ─── work orders ─────────────────────────────────────────────────────────────

def _wo_store():
    return {
        "work_orders": [
            {"id": 1, "section": "Projects", "title": "Fix pump", "status": "open",
             "priority": "high", "allocated_to": "Sam", "progress": 10,
             "updated_at": "2026-10-01T00:00:00+00:00"},
            {"id": 2, "section": "Mechanical", "title": "Fix fan", "status": "open",
             "priority": "low", "allocated_to": "Max", "progress": 0,
             "updated_at": "2026-10-01T00:00:00+00:00"},
        ],
        "work_order_comments": [
            {"id": 11, "work_order_id": 1, "comment": "Started"},
            {"id": 12, "work_order_id": 2, "comment": "Waiting parts"},
        ],
        "maintenance_events": [
            {"id": 21, "entity": "work_order", "entity_id": 1},
            {"id": 22, "entity": "work_order", "entity_id": 2},
        ],
    }


@pytest.fixture
def wo_client(monkeypatch):
    import app.routers.maintenance as mod
    return mount_router(monkeypatch, mod, "/api/maintenance", _wo_store())


def test_wo_scoped_list_is_predicated(wo_client):
    wo_client.as_user(SCOPED)
    resp = wo_client.get("/api/maintenance/work-orders")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]
    assert ("eq", "section", "Projects") in _predicates(wo_client)


def test_wo_scoped_single_of_other_section_is_404(wo_client):
    wo_client.as_user(SCOPED)
    assert wo_client.get("/api/maintenance/work-orders/2").status_code == 404
    assert wo_client.get("/api/maintenance/work-orders/1").status_code == 200


def _wo_payload(**over):
    payload = {
        "title": "Fix conveyor", "to_department": "Engineering", "to_section": "Plant",
        "date_raised": "2026-10-06", "work_order_number": "WO-9", "from_department": "Ops",
        "from_section": "Pit", "time_raised": "08:00", "account_number": "A-1",
        "equipment_info": "Pump", "user_lab_today": "yes", "job_type": {"maintenance": True},
        "job_request_details": "Fix it", "requested_by": "Pam",
        "authorising_foreman": "F", "authorising_engineer": "E", "allocated_to": "Sam",
        "estimated_hours": "2", "responsible_foreman": "F", "job_instructions": "Do it",
        "work_done_details": "", "cause_of_failure": "", "delay_details": "",
        "artisan_name": "Sam", "artisan_sign": "", "artisan_date": "",
        "foreman_name": "", "foreman_sign": "", "foreman_date": "",
        "time_work_started": "", "time_work_finished": "", "total_time_worked": "",
        "overtime_start_time": "", "overtime_end_time": "", "overtime_hours": "",
        "delay_from_time": "", "delay_to_time": "", "total_delay_hours": "",
    }
    payload.update(over)
    return payload


def test_wo_scoped_create_is_stamped_projects(wo_client):
    wo_client.as_user(SCOPED)
    resp = wo_client.post("/api/maintenance/work-orders", json=_wo_payload())
    assert resp.status_code == 200, resp.text
    assert wo_client.scope_store["work_orders"][-1]["section"] == "Projects"


def test_wo_scoped_move_out_writes_nothing(wo_client):
    wo_client.as_user(SCOPED)
    resp = wo_client.patch("/api/maintenance/work-orders/1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert wo_client.scope_store["work_orders"][0]["section"] == "Projects"


def test_wo_scoped_404_beats_stale_409(wo_client):
    wo_client.as_user(SCOPED)
    resp = wo_client.patch("/api/maintenance/work-orders/2", json={
        "title": "x", "expected_updated_at": "2000-01-01T00:00:00+00:00"})
    assert resp.status_code == 404


def test_wo_scoped_comments_follow_the_parent(wo_client):
    wo_client.as_user(SCOPED)
    assert wo_client.get("/api/maintenance/work-orders/2/comments").status_code == 404
    ours = wo_client.get("/api/maintenance/work-orders/1/comments")
    assert [c["id"] for c in ours.json()] == [11]
    resp = wo_client.post("/api/maintenance/work-orders/2/comments", json={"body": "hi"})
    assert resp.status_code == 404
    assert len(wo_client.scope_store["work_order_comments"]) == 2


def test_wo_scoped_events_follow_the_parent(wo_client):
    wo_client.as_user(SCOPED)
    assert wo_client.get("/api/maintenance/work-orders/2/events").status_code == 404
    ours = wo_client.get("/api/maintenance/work-orders/1/events")
    assert [e["id"] for e in ours.json()] == [21]


def test_wo_scoped_allocated_sees_the_slice(wo_client):
    wo_client.as_user(SCOPED)
    assert wo_client.get("/api/maintenance/work-orders/allocated/Max").json() == []
    assert [r["id"] for r in wo_client.get("/api/maintenance/work-orders/allocated/Sam").json()] == [1]


def test_wo_scoped_stats_see_the_slice_only(wo_client):
    wo_client.as_user(SCOPED)
    body = wo_client.get("/api/maintenance/work-orders/stats/summary").json()
    assert body["total_records"] == 1
    wo_client.as_user(UNSCOPED)
    assert wo_client.get("/api/maintenance/work-orders/stats/summary").json()["total_records"] == 2


# ─── breakdowns ──────────────────────────────────────────────────────────────

def _bd_store():
    return {"breakdowns": [
        {"id": "b1", "section": "Projects", "machine_id": "M-1", "machine_name": "Mill 1",
         "department": "Engineering", "location": "Plant", "breakdown_date": "2026-10-05",
         "breakdown_type": "mechanical", "artisan_name": "Sam", "status": "open",
         "downtime_minutes": 60},
        {"id": "b2", "section": "Mechanical", "machine_id": "M-2", "machine_name": "Mill 2",
         "department": "Engineering", "location": "Plant", "breakdown_date": "2026-10-05",
         "breakdown_type": "electrical", "artisan_name": "Max", "status": "open",
         "downtime_minutes": 120},
    ]}


@pytest.fixture
def bd_client(monkeypatch):
    import app.routers.breakdowns as mod
    return mount_router(monkeypatch, mod, "", _bd_store())


def test_bd_scoped_list_is_predicated(bd_client):
    bd_client.as_user(SCOPED)
    resp = bd_client.get("/api/breakdowns/get-breakdowns")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()["data"]] == ["b1"]
    assert ("eq", "section", "Projects") in _predicates(bd_client)


def test_bd_scoped_single_of_other_section_is_404(bd_client):
    bd_client.as_user(SCOPED)
    assert bd_client.get("/api/breakdowns/b2").status_code == 404
    assert bd_client.get("/api/breakdowns/b1").status_code == 200


def test_bd_scoped_create_is_stamped_projects(bd_client):
    bd_client.as_user(SCOPED)
    resp = bd_client.post("/api/breakdowns/", json={
        "machine_id": "M-3", "machine_name": "Mill 3", "department": "Engineering",
        "location": "Plant", "breakdown_type": "mechanical", "artisan_name": "Sam",
        "breakdown_date": "2026-10-06"})
    assert resp.status_code == 200, resp.text
    assert bd_client.scope_store["breakdowns"][-1]["section"] == "Projects"


def test_bd_scoped_move_out_writes_nothing(bd_client):
    bd_client.as_user(SCOPED)
    resp = bd_client.patch("/api/breakdowns/b1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert bd_client.scope_store["breakdowns"][0]["section"] == "Projects"


def test_bd_scoped_dashboard_sees_the_slice_only(bd_client):
    bd_client.as_user(SCOPED)
    body = bd_client.get("/api/breakdowns/dashboard/overview").json()
    assert body["metrics"]["total_breakdowns"] == 1


def test_bd_scoped_heatmap_sees_the_slice_only(bd_client):
    bd_client.as_user(SCOPED)
    body = bd_client.get("/api/breakdowns/analytics/heatmap").json()
    assert body["summary"]["total_breakdowns"] == 1
    assert body["summary"]["total_downtime_minutes"] == 60


# ─── notices (CrudRouter opt-in) ─────────────────────────────────────────────

def _no_store():
    return {"notices": [
        {"id": "n1", "section": "Projects", "title": "Blast at noon", "status": "active"},
        {"id": "n2", "section": "Mechanical", "title": "Meeting moved", "status": "active"},
    ]}


@pytest.fixture
def no_client(monkeypatch):
    import app.routers.notices as mod
    return mount_router(monkeypatch, mod, "/api/notices", _no_store())


def test_notices_scoped_list_is_predicated(no_client):
    no_client.as_user(SCOPED)
    resp = no_client.get("/api/notices")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == ["n1"]
    assert ("eq", "section", "Projects") in _predicates(no_client)


def test_notices_scoped_single_of_other_section_is_404(no_client):
    no_client.as_user(SCOPED)
    assert no_client.get("/api/notices/n2").status_code == 404
    assert no_client.get("/api/notices/n1").status_code == 200


def test_notices_scoped_create_is_stamped_projects(no_client):
    no_client.as_user(SCOPED)
    resp = no_client.post("/api/notices", json={"title": "Fume check", "content": "At 3pm", "date": "2026-10-06"})
    assert resp.status_code == 200, resp.text
    assert no_client.scope_store["notices"][-1]["section"] == "Projects"


def test_notices_scoped_stats_see_the_slice_only(no_client):
    no_client.as_user(SCOPED)
    body = no_client.get("/api/notices/stats/summary").json()
    assert body["total_notices"] == 1


# ─── sops ────────────────────────────────────────────────────────────────────

def _sop_store():
    return {
        "sop_documents": [
            {"id": "s1", "section": "Projects", "code": "SOP-1", "title": "Blast",
             "department": "Engineering", "deleted_at": None},
            {"id": "s2", "section": "Mechanical", "code": "SOP-2", "title": "Hoist",
             "department": "Engineering", "deleted_at": None},
        ],
        "sop_revisions": [
            {"id": "r1", "sop_id": "s1"},
            {"id": "r2", "sop_id": "s2"},
        ],
    }


@pytest.fixture
def sop_client(monkeypatch):
    import app.routers.sops as mod
    return mount_router(monkeypatch, mod, "", _sop_store())


def test_sops_scoped_list_is_predicated(sop_client):
    sop_client.as_user(SCOPED)
    resp = sop_client.get("/api/sops")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == ["s1"]
    assert ("eq", "section", "Projects") in _predicates(sop_client)


def test_sops_scoped_single_carries_own_revisions(sop_client):
    sop_client.as_user(SCOPED)
    assert sop_client.get("/api/sops/s2").status_code == 404
    ours = sop_client.get("/api/sops/s1")
    assert [r["id"] for r in ours.json()["revisions"]] == ["r1"]


def test_sops_scoped_manager_create_is_stamped_projects(sop_client):
    sop_client.as_user(SCOPED_MGR)
    resp = sop_client.post("/api/sops", json={
        "code": "SOP-9", "title": "Decline", "department": "Engineering", "owner": "Pam"})
    assert resp.status_code == 200, resp.text
    assert sop_client.scope_store["sop_documents"][-1]["section"] == "Projects"


def test_sops_scoped_code_check_sees_visible_rows_only(sop_client):
    sop_client.as_user(SCOPED_MGR)
    assert sop_client.post("/api/sops", json={
        "code": "SOP-2", "title": "Copy", "department": "Engineering", "owner": "Pam"}).status_code == 200
    clash = sop_client.post("/api/sops", json={
        "code": "SOP-1", "title": "Copy", "department": "Engineering", "owner": "Pam"})
    assert clash.status_code == 409


def test_sops_scoped_move_out_writes_nothing(sop_client):
    sop_client.as_user(SCOPED)
    resp = sop_client.patch("/api/sops/s1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert sop_client.scope_store["sop_documents"][0]["section"] == "Projects"


def test_sops_scoped_archived_sees_the_slice(sop_client):
    sop_client.as_user(SCOPED_MGR)
    sop_client.patch("/api/sops/s1", json={"title": "Blast v2"})
    sop_client.post("/api/sops/s1/archive")
    assert [r["id"] for r in sop_client.get("/api/sops/archived").json()] == ["s1"]


# ─── services ────────────────────────────────────────────────────────────────

def _svc_store():
    return {
        "services": [
            {"id": "sv1", "section": "Projects", "description": "Pump overhaul",
             "stage_signatures": {}},
            {"id": "sv2", "section": "Mechanical", "description": "Fan repair",
             "stage_signatures": {}},
        ],
        "service_attachments": [
            {"id": "a1", "service_id": "sv1", "filename": "quote.pdf"},
            {"id": "a2", "service_id": "sv2", "filename": "invoice.pdf"},
        ],
    }


@pytest.fixture
def svc_client(monkeypatch):
    import app.routers.services as mod
    return mount_router(monkeypatch, mod, "", _svc_store())


def test_services_scoped_list_is_predicated(svc_client):
    svc_client.as_user(SCOPED)
    resp = svc_client.get("/api/services")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == ["sv1"]
    assert ("eq", "section", "Projects") in _predicates(svc_client)


def test_services_scoped_create_is_stamped_projects(svc_client):
    svc_client.as_user(SCOPED)
    resp = svc_client.post("/api/services", json={"description": "Valve job"})
    assert resp.status_code == 200, resp.text
    assert svc_client.scope_store["services"][-1]["section"] == "Projects"


def test_services_scoped_move_out_writes_nothing(svc_client):
    svc_client.as_user(SCOPED)
    resp = svc_client.put("/api/services/sv1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert svc_client.scope_store["services"][0]["section"] == "Projects"


def test_services_scoped_signatures_follow_the_parent(svc_client):
    svc_client.as_user(SCOPED)
    assert svc_client.get("/api/services/sv2/signatures").status_code == 404
    assert svc_client.get("/api/services/sv1/signatures").status_code == 200


def test_services_scoped_attachments_follow_the_parent(svc_client):
    svc_client.as_user(SCOPED)
    assert svc_client.get("/api/services/sv2/attachments").status_code == 404
    ours = svc_client.get("/api/services/sv1/attachments")
    assert [a["id"] for a in ours.json()] == ["a1"]


def test_services_scoped_manager_cannot_delete_other_attachment(svc_client):
    svc_client.as_user(SCOPED_MGR)
    assert svc_client.delete("/api/services/sv2/attachments/a2").status_code == 404
    assert len(svc_client.scope_store["service_attachments"]) == 2
    svc_client.as_user(UNSCOPED)
    assert svc_client.delete("/api/services/sv2/attachments/a2").status_code == 200
    assert len(svc_client.scope_store["service_attachments"]) == 1
