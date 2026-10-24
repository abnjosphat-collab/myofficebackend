# tests/test_scope_batch3.py — Batch 3 direct-section enforcement.
#
# Near-miss, work stoppage, SHEQ inspections/findings, PTO, requisitions, plus
# the direct-section orphans the PLAN left unnamed (handovers, job cards,
# lubrication, safety complaints): a scoped viewer lists their slice, reads or
# writes anything else as 404 (writing nothing), creates land stamped, and
# stats/suggestions/counts see the slice only. Unscoped callers run the exact
# queries they always did.

import pytest

from tests.scope_harness import (
    SCOPED, SCOPED_MGR, UNSCOPED, mount_router,
)


def _predicates(client):
    return [c for c in client.scope_calls if c[0] in ("eq", "in")]


# ─── near miss ───────────────────────────────────────────────────────────────

def _nm_store():
    return {"nearmiss_reports": [
        {"id": "nm1", "section": "Projects", "department": "Engineering",
         "date": "2026-10-01", "time": "08:00", "location": "Shaft",
         "description": "Slipped near the sump, no injury.", "reportername": "Pam"},
        {"id": "nm2", "section": "Mechanical", "department": "Engineering",
         "date": "2026-10-02", "time": "09:00", "location": "Plant",
         "description": "Oil spill by the compressor.", "reportername": "Max"},
    ]}


@pytest.fixture
def nm_client(monkeypatch):
    import app.routers.near_miss as mod
    return mount_router(monkeypatch, mod, "", _nm_store())


def test_nearmiss_scoped_list_is_predicated(nm_client):
    nm_client.as_user(SCOPED)
    resp = nm_client.get("/api/nearmiss")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == ["nm1"]
    assert ("eq", "section", "Projects") in _predicates(nm_client)


def test_nearmiss_unscoped_list_query_is_unchanged(nm_client):
    nm_client.as_user(UNSCOPED)
    resp = nm_client.get("/api/nearmiss")
    assert [r["id"] for r in resp.json()] == ["nm1", "nm2"]
    assert _predicates(nm_client) == []


def test_nearmiss_scoped_single_of_other_section_is_404(nm_client):
    nm_client.as_user(SCOPED)
    assert nm_client.get("/api/nearmiss/nm2").status_code == 404
    assert nm_client.get("/api/nearmiss/nm1").status_code == 200


def test_nearmiss_scoped_create_is_stamped_projects(nm_client):
    nm_client.as_user(SCOPED)
    resp = nm_client.post("/api/nearmiss", json={
        "department": "Engineering", "section": "General",
        "date": "2026-10-06", "time": "10:00", "location": "Shaft",
        "description": "Loose cable across the walkway."})
    assert resp.status_code == 200, resp.text
    assert resp.json()["section"] == "Projects"
    assert nm_client.scope_store["nearmiss_reports"][-1]["section"] == "Projects"


def test_nearmiss_projects_passes_section_validation(nm_client):
    nm_client.as_user(SCOPED)
    resp = nm_client.post("/api/nearmiss", json={
        "department": "Engineering", "section": "Projects",
        "date": "2026-10-06", "time": "10:00", "location": "Shaft",
        "description": "Loose cable across the walkway."})
    assert resp.status_code == 200, resp.text


def test_nearmiss_scoped_move_out_writes_nothing(nm_client):
    nm_client.as_user(SCOPED)
    resp = nm_client.patch("/api/nearmiss/nm1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert nm_client.scope_store["nearmiss_reports"][0]["section"] == "Projects"


def test_nearmiss_scoped_patch_inside_section_works(nm_client):
    nm_client.as_user(SCOPED)
    resp = nm_client.patch("/api/nearmiss/nm1", json={"location": "Decline"})
    assert resp.status_code == 200
    assert nm_client.scope_store["nearmiss_reports"][0]["location"] == "Decline"


def test_nearmiss_scoped_stats_see_the_slice_only(nm_client):
    nm_client.as_user(SCOPED)
    body = nm_client.get("/api/nearmiss/stats/overview").json()
    assert body["total"] == 1
    assert body["bySection"]["Projects"] == 1
    assert body["byReporter"] == {"Pam": 1}


# ─── work stoppage ────────────────────────────────────────────────────────────

def _ws_store():
    return {
        "work_stoppage_reports": [
            {"id": "ws1", "section": "Projects", "department": "Engineering",
             "date": "2026-10-01", "description": "Unsafe roof bolts found.",
             "stoppage_by": "Sam"},
            {"id": "ws2", "section": "Mechanical", "department": "Mining",
             "date": "2026-10-02", "description": "Guard missing on belt.",
             "stoppage_by": "Max"},
        ],
        "corrective_actions": [
            {"id": "wa1", "report_id": "ws1", "status": "Pending"},
            {"id": "wa2", "report_id": "ws2", "status": "Completed"},
        ],
    }


@pytest.fixture
def ws_client(monkeypatch):
    import app.routers.work_stoppage as mod
    return mount_router(monkeypatch, mod, "", _ws_store())


def test_stoppage_scoped_list_sees_slice_with_actions(ws_client):
    ws_client.as_user(SCOPED)
    resp = ws_client.get("/api/work-stoppage")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["id"] for r in rows] == ["ws1"]
    assert [a["id"] for a in rows[0]["correctiveActions"]] == ["wa1"]
    assert ("eq", "section", "Projects") in _predicates(ws_client)


def test_stoppage_scoped_single_of_other_section_is_404(ws_client):
    ws_client.as_user(SCOPED)
    assert ws_client.get("/api/work-stoppage/ws2").status_code == 404
    assert ws_client.get("/api/work-stoppage/ws1").status_code == 200


def test_stoppage_scoped_suggestions_come_from_the_slice(ws_client):
    ws_client.as_user(SCOPED)
    assert ws_client.get("/api/work-stoppage/suggestions/departments").json() == ["Engineering"]
    assert ws_client.get("/api/work-stoppage/suggestions/inspectors").json() == ["Sam"]
    ws_client.as_user(UNSCOPED)
    assert ws_client.get("/api/work-stoppage/suggestions/departments").json() == ["Engineering", "Mining"]


def test_stoppage_scoped_create_is_stamped_projects(ws_client):
    ws_client.as_user(SCOPED)
    resp = ws_client.post("/api/work-stoppage", json={
        "date": "2026-10-06", "department": "Engineering", "section": "General",
        "description": "Water ingress at the decline.", "stoppageBy": "Sam",
        "correctiveActions": [{"finding": "Pump out", "action": "Pump",
                               "byWho": "Sam", "byWhen": "2026-10-07"}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["section"] == "Projects"
    stored = ws_client.scope_store["work_stoppage_reports"][-1]
    assert stored["section"] == "Projects"
    assert ws_client.scope_store["corrective_actions"][-1]["report_id"] == stored["id"]


def test_stoppage_scoped_move_out_writes_nothing(ws_client):
    ws_client.as_user(SCOPED)
    resp = ws_client.patch("/api/work-stoppage/ws1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert ws_client.scope_store["work_stoppage_reports"][0]["section"] == "Projects"


def test_stoppage_scoped_stats_see_the_slice_only(ws_client):
    ws_client.as_user(SCOPED)
    body = ws_client.get("/api/work-stoppage/stats/overview").json()
    assert body["total"] == 1
    assert body["bySection"]["Projects"] == 1
    assert body["pendingActions"] == 1
    assert body["completedActions"] == 0


# ─── sheq inspections + findings ──────────────────────────────────────────────

def _sheq_store():
    return {
        "sheq_inspections": [
            {"id": "in1", "section": "Projects", "title": "Shaft check",
             "inspectors": "Pam", "status": "draft"},
            {"id": "in2", "section": "Mechanical", "title": "Plant check",
             "inspectors": "Max", "status": "draft"},
        ],
        "sheq_findings": [
            {"id": "f1", "inspection_id": "in1", "section": "General",
             "status": "open", "priority": "critical"},
            {"id": "f2", "inspection_id": "in2", "section": "Mechanical",
             "status": "open", "priority": "low"},
        ],
    }


@pytest.fixture
def sheq_client(monkeypatch):
    import app.routers.sheq_inspections as mod
    return mount_router(monkeypatch, mod, "", _sheq_store())


def test_sheq_scoped_list_is_predicated(sheq_client):
    sheq_client.as_user(SCOPED)
    resp = sheq_client.get("/api/sheq")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["id"] for r in rows] == ["in1"]
    assert [f["id"] for f in rows[0]["findings"]] == ["f1"]
    assert ("eq", "section", "Projects") in _predicates(sheq_client)


def test_sheq_scoped_single_of_other_section_is_404(sheq_client):
    sheq_client.as_user(SCOPED)
    assert sheq_client.get("/api/sheq/in2").status_code == 404
    assert sheq_client.get("/api/sheq/in1").status_code == 200


def test_sheq_scoped_findings_follow_the_parent(sheq_client):
    sheq_client.as_user(SCOPED)
    assert sheq_client.get("/api/sheq/in2/findings").status_code == 404
    found = sheq_client.get("/api/sheq/in1/findings")
    assert [f["id"] for f in found.json()] == ["f1"]


def test_sheq_scoped_finding_post_to_other_parent_writes_nothing(sheq_client):
    sheq_client.as_user(SCOPED)
    resp = sheq_client.post("/api/sheq/in2/findings", json={
        "finding": "Wire exposed", "requiredAction": "Cover it",
        "byWho": "Max", "byWhen": "2026-10-08", "section": "Mechanical"})
    assert resp.status_code == 404
    assert len(sheq_client.scope_store["sheq_findings"]) == 2


def test_sheq_scoped_finding_post_is_stamped_projects(sheq_client):
    sheq_client.as_user(SCOPED)
    resp = sheq_client.post("/api/sheq/in1/findings", json={
        "finding": "Guard missing", "requiredAction": "Fit guard",
        "byWho": "Pam", "byWhen": "2026-10-08", "section": "Mechanical"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["section"] == "Projects"
    assert sheq_client.scope_store["sheq_findings"][-1]["section"] == "Projects"


def test_sheq_scoped_finding_patch_move_is_404(sheq_client):
    sheq_client.as_user(SCOPED)
    resp = sheq_client.patch("/api/sheq/findings/f1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert sheq_client.scope_store["sheq_findings"][0]["section"] == "General"


def test_sheq_scoped_finding_patch_of_other_parent_is_404(sheq_client):
    sheq_client.as_user(SCOPED)
    assert sheq_client.patch("/api/sheq/findings/f2", json={"status": "closed"}).status_code == 404
    assert sheq_client.scope_store["sheq_findings"][1]["status"] == "open"


def test_sheq_scoped_finding_keep_section_is_allowed(sheq_client):
    sheq_client.as_user(SCOPED)
    resp = sheq_client.patch("/api/sheq/findings/f1", json={"section": "General", "status": "closed"})
    assert resp.status_code == 200
    assert sheq_client.scope_store["sheq_findings"][0]["status"] == "closed"


def test_sheq_scoped_create_stamps_inspection_and_findings(sheq_client):
    sheq_client.as_user(SCOPED)
    resp = sheq_client.post("/api/sheq", json={
        "inspectors": "Pam", "title": "Decline check", "place": "Decline",
        "date": "2026-10-06", "time": "08:00", "section": "Mechanical",
        "findings": [{"finding": "Bolt loose", "requiredAction": "Tighten",
                      "byWho": "Pam", "byWhen": "2026-10-07", "section": "Mechanical"}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["section"] == "Projects"
    assert resp.json()["findings"][0]["section"] == "Projects"
    assert sheq_client.scope_store["sheq_inspections"][-1]["section"] == "Projects"


def test_sheq_scoped_move_out_writes_nothing(sheq_client):
    sheq_client.as_user(SCOPED)
    resp = sheq_client.patch("/api/sheq/in1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert sheq_client.scope_store["sheq_inspections"][0]["section"] == "Projects"


def test_sheq_scoped_stats_see_the_slice_only(sheq_client):
    sheq_client.as_user(SCOPED)
    body = sheq_client.get("/api/sheq/stats/overview").json()
    assert body["total"] == 1
    assert body["open"] == 1
    assert body["critical"] == 1
    assert body["bySection"]["Projects"] == 1


# ─── pto ─────────────────────────────────────────────────────────────────────

def _pto_store():
    return {
        "pto_reports": [
            {"id": "pt1", "section": "Projects", "observer_name": "Pam",
             "status": "draft", "observation_type": "Initial",
             "risk_assessment": {"made": "No", "identified": "Yes", "effective": "Yes"}},
            {"id": "pt2", "section": "Mechanical", "observer_name": "Max",
             "status": "closed", "observation_type": "Follow up",
             "risk_assessment": {"made": "Yes", "identified": "Yes", "effective": "Yes"}},
        ],
        "pto_action_plan": [
            {"id": "pa1", "report_id": "pt1", "status": "Pending"},
            {"id": "pa2", "report_id": "pt2", "status": "Completed"},
        ],
    }


@pytest.fixture
def pto_client(monkeypatch):
    import app.routers.pto as mod
    return mount_router(monkeypatch, mod, "", _pto_store())


def _pto_payload(section="Mechanical"):
    return {
        "date": "2026-10-06", "observerName": "Pam", "section": section,
        "workerName": "Pat", "jobTaskObserved": "Rigging the pump",
        "observationType": "Initial", "timeOnJob": {}, "notification": {},
        "reasons": {}, "procedures": {}, "riskAssessment": {},
        "suggestedRemedies": {}, "observationScope": "All", "followUpNeeded": "No"}


def test_pto_scoped_list_is_predicated(pto_client):
    pto_client.as_user(SCOPED)
    resp = pto_client.get("/api/pto")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["id"] for r in rows] == ["pt1"]
    assert [a["id"] for a in rows[0]["actionPlan"]] == ["pa1"]
    assert ("eq", "section", "Projects") in _predicates(pto_client)


def test_pto_scoped_suggestions_come_from_the_slice(pto_client):
    pto_client.as_user(SCOPED)
    assert pto_client.get("/api/pto/suggestions/observers").json() == ["Pam"]


def test_pto_scoped_single_of_other_section_is_404(pto_client):
    pto_client.as_user(SCOPED)
    assert pto_client.get("/api/pto/pt2").status_code == 404
    assert pto_client.get("/api/pto/pt1").status_code == 200


def test_pto_scoped_create_is_stamped_projects(pto_client):
    pto_client.as_user(SCOPED)
    resp = pto_client.post("/api/pto", json=_pto_payload())
    assert resp.status_code == 200, resp.text
    assert resp.json()["section"] == "Projects"
    assert pto_client.scope_store["pto_reports"][-1]["section"] == "Projects"


def test_pto_projects_passes_section_validation(pto_client):
    pto_client.as_user(SCOPED)
    resp = pto_client.post("/api/pto", json=_pto_payload("Projects"))
    assert resp.status_code == 200, resp.text


def test_pto_scoped_move_out_writes_nothing(pto_client):
    pto_client.as_user(SCOPED)
    resp = pto_client.patch("/api/pto/pt1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert pto_client.scope_store["pto_reports"][0]["section"] == "Projects"


def test_pto_scoped_stats_see_the_slice_only(pto_client):
    pto_client.as_user(SCOPED)
    body = pto_client.get("/api/pto/stats/overview").json()
    assert body["total"] == 1
    assert body["bySection"]["Projects"] == 1
    assert body["pendingActions"] == 1
    assert body["completedActions"] == 0
    assert body["highRiskCount"] == 1
    assert body["initialObservations"] == 1
    assert body["draftCount"] == 1


# ─── requisitions ────────────────────────────────────────────────────────────

def _req_store():
    return {
        "requisitions": [
            {"id": 1, "section": "Projects", "status": "open",
             "date": "2026-10-05", "requisition_number": "REQ-1"},
            {"id": 2, "section": "Mechanical", "status": "open",
             "date": "2026-10-05", "requisition_number": "REQ-2"},
            {"id": 3, "section": "Projects", "status": "closed",
             "date": "2026-10-04", "requisition_number": "REQ-3"},
        ],
        "requisition_items": [
            {"id": 11, "requisition_id": 1, "cost_per_unit": 10.0, "quantity": 2},
            {"id": 12, "requisition_id": 2, "cost_per_unit": 100.0, "quantity": 1},
            {"id": 13, "requisition_id": 3, "cost_per_unit": 5.0, "quantity": 1},
        ],
    }


@pytest.fixture
def req_client(monkeypatch):
    import app.routers.requisitions as mod
    return mount_router(monkeypatch, mod, "/api/requisitions", _req_store())


def _req_payload(number="REQ-9", section="Mechanical"):
    return {"date": "2026-10-06", "requester": "Pam", "section": section,
            "priority": "high", "status": "open", "requisition_number": number,
            "items": [{"description": "Bolts", "cost_per_unit": 2.0, "quantity": 5}]}


def test_req_scoped_list_is_predicated(req_client):
    req_client.as_user(SCOPED)
    resp = req_client.get("/api/requisitions")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1, 3]
    assert ("eq", "section", "Projects") in _predicates(req_client)


def test_req_scoped_single_counts_the_slice_line(req_client):
    req_client.as_user(SCOPED)
    assert req_client.get("/api/requisitions/2").status_code == 404
    one = req_client.get("/api/requisitions/1")
    assert one.status_code == 200
    assert one.json()["line_number"] == 1
    three = req_client.get("/api/requisitions/3")
    assert three.json()["line_number"] == 2


def test_req_scoped_create_is_stamped_projects(req_client):
    req_client.as_user(SCOPED)
    resp = req_client.post("/api/requisitions", json=_req_payload())
    assert resp.status_code == 200, resp.text
    assert resp.json()["section"] == "Projects"
    assert resp.json()["line_number"] == 3
    assert req_client.scope_store["requisitions"][-1]["section"] == "Projects"


def test_req_scoped_number_check_sees_visible_rows_only(req_client):
    req_client.as_user(SCOPED)
    # REQ-2 belongs to Mechanical: invisible, so no 409-style refusal.
    assert req_client.post("/api/requisitions", json=_req_payload("REQ-2")).status_code == 200
    # REQ-1 is a Projects number: still refused.
    clash = req_client.post("/api/requisitions", json=_req_payload("REQ-1"))
    assert clash.status_code == 400


def test_req_scoped_patch_number_check_sees_visible_rows_only(req_client):
    req_client.as_user(SCOPED)
    assert req_client.patch("/api/requisitions/1", json={"requisition_number": "REQ-2"}).status_code == 200
    assert req_client.patch("/api/requisitions/1", json={"requisition_number": "REQ-3"}).status_code == 400


def test_req_scoped_move_out_writes_nothing(req_client):
    req_client.as_user(SCOPED)
    resp = req_client.patch("/api/requisitions/1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert req_client.scope_store["requisitions"][0]["section"] == "Projects"


def test_req_scoped_stats_see_the_slice_only(req_client):
    req_client.as_user(SCOPED)
    body = req_client.get("/api/requisitions/stats/summary").json()
    assert body["total_requisitions"] == 2
    assert body["total_cost"] == 25.0
    assert body["section_breakdown"] == {"Projects": 2}


def test_req_scoped_daily_total_sees_the_slice_only(req_client):
    req_client.as_user(SCOPED)
    body = req_client.get("/api/requisitions/daily-total/2026-10-05").json()
    assert body["total"] == 20.0
    req_client.as_user(UNSCOPED)
    body = req_client.get("/api/requisitions/daily-total/2026-10-05").json()
    assert body["total"] == 120.0


# ─── handovers (CrudRouter opt-in) ───────────────────────────────────────────

def _ho_store():
    return {"shift_handovers": [
        {"id": 1, "section": "Projects", "shift": "day",
         "handover_date": "2026-10-06", "outgoing_supervisor": "Pam"},
        {"id": 2, "section": "Mechanical", "shift": "night",
         "handover_date": "2026-10-06", "outgoing_supervisor": "Max"},
    ]}


@pytest.fixture
def ho_client(monkeypatch):
    import app.routers.handover as mod
    return mount_router(monkeypatch, mod, "/api/handover", _ho_store())


def test_handover_scoped_list_is_predicated(ho_client):
    ho_client.as_user(SCOPED)
    resp = ho_client.get("/api/handover")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]
    assert ("eq", "section", "Projects") in _predicates(ho_client)


def test_handover_scoped_single_of_other_section_is_404(ho_client):
    ho_client.as_user(SCOPED)
    assert ho_client.get("/api/handover/2").status_code == 404
    assert ho_client.get("/api/handover/1").status_code == 200


def test_handover_scoped_create_is_stamped_projects(ho_client):
    ho_client.as_user(SCOPED)
    resp = ho_client.post("/api/handover", json={
        "handover_date": "2026-10-07", "shift": "day",
        "outgoing_supervisor": "Pam"})
    assert resp.status_code == 200, resp.text
    assert ho_client.scope_store["shift_handovers"][-1]["section"] == "Projects"


def test_handover_scoped_move_out_writes_nothing(ho_client):
    ho_client.as_user(SCOPED)
    resp = ho_client.patch("/api/handover/1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert ho_client.scope_store["shift_handovers"][0]["section"] == "Projects"


# ─── job cards (CrudRouter opt-in + hand patch) ──────────────────────────────

def _jc_store():
    return {"job_cards": [
        {"id": 1, "section": "Projects", "job_no": "JC-1", "title": "Fix pump",
         "updated_at": "2026-10-01T00:00:00+00:00"},
        {"id": 2, "section": "Mechanical", "job_no": "JC-2", "title": "Fix fan",
         "updated_at": "2026-10-01T00:00:00+00:00"},
    ]}


@pytest.fixture
def jc_client(monkeypatch):
    import app.routers.job_cards as mod
    return mount_router(monkeypatch, mod, "/api/job-cards", _jc_store())


def test_jobcard_scoped_list_is_predicated(jc_client):
    jc_client.as_user(SCOPED)
    resp = jc_client.get("/api/job-cards")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]
    assert ("eq", "section", "Projects") in _predicates(jc_client)


def test_jobcard_scoped_single_of_other_section_is_404(jc_client):
    jc_client.as_user(SCOPED)
    assert jc_client.get("/api/job-cards/2").status_code == 404
    assert jc_client.get("/api/job-cards/1").status_code == 200


def test_jobcard_scoped_move_out_writes_nothing(jc_client):
    jc_client.as_user(SCOPED)
    resp = jc_client.patch("/api/job-cards/1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert jc_client.scope_store["job_cards"][0]["section"] == "Projects"


def test_jobcard_scoped_404_beats_stale_409(jc_client):
    jc_client.as_user(SCOPED)
    resp = jc_client.patch("/api/job-cards/2", json={
        "title": "x", "expected_updated_at": "2000-01-01T00:00:00+00:00"})
    assert resp.status_code == 404


def test_jobcard_stale_patch_inside_section_still_409s(jc_client):
    jc_client.as_user(SCOPED)
    resp = jc_client.patch("/api/job-cards/1", json={
        "title": "x", "expected_updated_at": "2000-01-01T00:00:00+00:00"})
    assert resp.status_code == 409


# ─── lubrication (schedules direct, records inherit) ─────────────────────────

def _lube_store():
    return {
        "lube_schedules": [
            {"id": 1, "section": "Projects", "equipment_name": "Mill 1",
             "lube_point": "Bearing", "lubricant_type": "Grease"},
            {"id": 2, "section": "Mechanical", "equipment_name": "Mill 2",
             "lube_point": "Bearing", "lubricant_type": "Grease"},
        ],
        "lube_records": [
            {"id": 11, "schedule_id": 1, "equipment_name": "Mill 1",
             "lube_point": "Bearing", "done_date": "2026-10-01"},
            {"id": 12, "schedule_id": 2, "equipment_name": "Mill 2",
             "lube_point": "Bearing", "done_date": "2026-10-02"},
            {"id": 13, "schedule_id": None, "equipment_name": "Pump",
             "lube_point": "Seal", "done_date": "2026-10-03"},
        ],
    }


@pytest.fixture
def lube_client(monkeypatch):
    import app.routers.lubrication as mod
    return mount_router(monkeypatch, mod, "/api/lubrication", _lube_store())


def test_lube_scoped_schedules_are_predicated(lube_client):
    lube_client.as_user(SCOPED)
    resp = lube_client.get("/api/lubrication")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]
    assert ("eq", "section", "Projects") in _predicates(lube_client)


def test_lube_scoped_create_is_stamped_projects(lube_client):
    lube_client.as_user(SCOPED)
    resp = lube_client.post("/api/lubrication", json={
        "equipment_name": "Mill 3", "lube_point": "Bearing",
        "lubricant_type": "Grease", "section": "Mechanical"})
    assert resp.status_code == 200, resp.text
    assert lube_client.scope_store["lube_schedules"][-1]["section"] == "Projects"


def test_lube_scoped_move_out_writes_nothing(lube_client):
    lube_client.as_user(SCOPED)
    resp = lube_client.patch("/api/lubrication/1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert lube_client.scope_store["lube_schedules"][0]["section"] == "Projects"


def test_lube_scoped_records_follow_the_schedule(lube_client):
    lube_client.as_user(SCOPED)
    resp = lube_client.get("/api/lubrication/records")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [11]


def test_lube_scoped_record_create_to_other_schedule_writes_nothing(lube_client):
    lube_client.as_user(SCOPED)
    resp = lube_client.post("/api/lubrication/records", json={
        "schedule_id": 2, "equipment_name": "Mill 2", "lube_point": "Bearing",
        "done_date": "2026-10-06"})
    assert resp.status_code == 404
    assert len(lube_client.scope_store["lube_records"]) == 3


def test_lube_scoped_unlinked_record_create_is_404(lube_client):
    lube_client.as_user(SCOPED)
    resp = lube_client.post("/api/lubrication/records", json={
        "equipment_name": "Pump", "lube_point": "Seal", "done_date": "2026-10-06"})
    assert resp.status_code == 404
    lube_client.as_user(UNSCOPED)
    assert lube_client.post("/api/lubrication/records", json={
        "equipment_name": "Pump", "lube_point": "Seal", "done_date": "2026-10-06"}).status_code == 200


# ─── safety complaints (General stays open) ──────────────────────────────────

def _sc_store():
    return {"safety_complaints": [
        {"id": "c1", "section": "Projects", "date": "2026-10-04",
         "issue_raised": "Dust in the decline.", "priority": "medium", "status": "open"},
        {"id": "c2", "section": "Mechanical", "date": "2026-10-03",
         "issue_raised": "Noise in the plant.", "priority": "medium", "status": "open"},
        {"id": "c3", "section": "General", "date": "2026-10-02",
         "issue_raised": "Gate left open.", "priority": "low", "status": "open"},
        {"id": "c4", "section": "", "date": "2026-10-01",
         "issue_raised": "Ponding near offices.", "priority": "low", "status": "open"},
    ]}


@pytest.fixture
def sc_client(monkeypatch):
    import app.routers.safety_complaints as mod
    return mount_router(monkeypatch, mod, "", _sc_store())


def test_complaints_scoped_list_keeps_general_open(sc_client):
    sc_client.as_user(SCOPED)
    resp = sc_client.get("/api/safety-complaints")
    assert resp.status_code == 200
    assert {r["id"] for r in resp.json()} == {"c1", "c3", "c4"}


def test_complaints_scoped_pagination_slices_the_visible(sc_client):
    sc_client.as_user(SCOPED)
    resp = sc_client.get("/api/safety-complaints", params={"limit": 2, "offset": 0})
    assert [r["id"] for r in resp.json()] == ["c1", "c3"]
    resp = sc_client.get("/api/safety-complaints", params={"limit": 2, "offset": 2})
    assert [r["id"] for r in resp.json()] == ["c4"]


def test_complaints_scoped_general_single_is_visible(sc_client):
    sc_client.as_user(SCOPED)
    assert sc_client.get("/api/safety-complaints/c3").status_code == 200
    assert sc_client.get("/api/safety-complaints/c2").status_code == 404


def test_complaints_scoped_create_is_stamped_projects(sc_client):
    sc_client.as_user(SCOPED)
    resp = sc_client.post("/api/safety-complaints", json={
        "date": "2026-10-06", "issue_raised": "Spill by the bund.", "section": "General"})
    assert resp.status_code == 201, resp.text
    assert sc_client.scope_store["safety_complaints"][-1]["section"] == "Projects"


def test_complaints_scoped_edit_of_general_row_works(sc_client):
    sc_client.as_user(SCOPED)
    resp = sc_client.patch("/api/safety-complaints/c3", json={"priority": "high"})
    assert resp.status_code == 200
    assert sc_client.scope_store["safety_complaints"][2]["priority"] == "high"


def test_complaints_scoped_keep_general_section_is_allowed(sc_client):
    sc_client.as_user(SCOPED)
    resp = sc_client.patch("/api/safety-complaints/c3", json={"section": "General", "priority": "high"})
    assert resp.status_code == 200
    assert sc_client.scope_store["safety_complaints"][2]["priority"] == "high"


def test_complaints_scoped_section_move_is_404(sc_client):
    sc_client.as_user(SCOPED)
    resp = sc_client.patch("/api/safety-complaints/c1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert sc_client.scope_store["safety_complaints"][0]["section"] == "Projects"


def test_complaints_scoped_manager_cannot_delete_other_section(sc_client):
    sc_client.as_user(SCOPED_MGR)
    assert sc_client.delete("/api/safety-complaints/c2").status_code == 404
    assert len(sc_client.scope_store["safety_complaints"]) == 4
    sc_client.as_user(UNSCOPED)
    assert sc_client.delete("/api/safety-complaints/c2").status_code == 204
    assert len(sc_client.scope_store["safety_complaints"]) == 3
