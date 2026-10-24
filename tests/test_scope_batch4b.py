# tests/test_scope_batch4b.py — Batch 4 enforcement: daily reports, pachedu,
# VFL, maintenance schedules, availability, compliance register, condition
# monitoring. Same contract as batch4a: scoped viewers list their slice,
# read or write anything else as 404 (writing nothing), creates land
# stamped, and stats see the slice only.

from datetime import date, timedelta

import pytest

from tests.scope_harness import (
    SCOPED, SCOPED_MGR, UNSCOPED, mount_router,
)


def _predicates(client):
    return [c for c in client.scope_calls if c[0] in ("eq", "in")]


# ─── daily reports (upsert grain is date × section for scoped) ───────────────

def _dr_store():
    return {"daily_reports": [
        {"id": 1, "date": "2026-10-05", "section": "Projects",
         "plant_availability_percent": 95.0, "dam_level": 7.0, "call_outs": []},
        {"id": 2, "date": "2026-10-05", "section": "Mechanical",
         "plant_availability_percent": 90.0, "dam_level": 6.0, "call_outs": []},
        {"id": 3, "date": "2026-10-04", "section": "Projects",
         "plant_availability_percent": 96.0, "dam_level": 7.1, "call_outs": []},
    ]}


@pytest.fixture
def dr_client(monkeypatch):
    import app.routers.daily_reports as mod
    return mount_router(monkeypatch, mod, "/api/daily-reports", _dr_store())


def test_daily_scoped_list_is_predicated(dr_client):
    dr_client.as_user(SCOPED)
    resp = dr_client.get("/api/daily-reports/")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1, 3]
    assert ("eq", "section", "Projects") in _predicates(dr_client)


def test_daily_scoped_upsert_creates_alongside_other_section(dr_client):
    # Grain is date × section: with no Projects row for the date, the scoped
    # POST creates one next to the Mechanical row instead of touching it.
    dr_client.scope_store["daily_reports"] = [
        r for r in dr_client.scope_store["daily_reports"] if r["id"] != 1
    ]
    dr_client.as_user(SCOPED)
    resp = dr_client.post("/api/daily-reports/", json={"date": "2026-10-05", "notes": "P day"})
    assert resp.status_code == 200, resp.text
    same_date = [r for r in dr_client.scope_store["daily_reports"] if r["date"] == "2026-10-05"]
    assert sorted(r["section"] for r in same_date) == ["Mechanical", "Projects"]


def test_daily_scoped_upsert_updates_own_row(dr_client):
    dr_client.as_user(SCOPED)
    resp = dr_client.post("/api/daily-reports/", json={"date": "2026-10-04", "notes": "Updated"})
    assert resp.status_code == 200, resp.text
    assert len(dr_client.scope_store["daily_reports"]) == 3
    assert dr_client.scope_store["daily_reports"][2]["notes"] == "Updated"


def test_daily_scoped_move_out_writes_nothing(dr_client):
    dr_client.as_user(SCOPED)
    resp = dr_client.post("/api/daily-reports/", json={"date": "2026-10-04", "section": "Mechanical"})
    assert resp.status_code == 404
    assert dr_client.scope_store["daily_reports"][2]["section"] == "Projects"


def test_daily_scoped_stats_see_the_slice_only(dr_client):
    dr_client.as_user(SCOPED)
    body = dr_client.get("/api/daily-reports/stats/summary").json()
    assert body["total_reports"] == 2
    assert body["avg_plant_availability"] == 95.5


def test_daily_scoped_trends_see_the_slice_only(dr_client):
    dr_client.as_user(SCOPED)
    body = dr_client.get("/api/daily-reports/trends/plant-availability").json()
    assert body["dates"] == ["2026-10-04", "2026-10-05"]


def test_daily_scoped_manager_delete_all_keeps_other_section(dr_client):
    dr_client.as_user(SCOPED_MGR)
    resp = dr_client.delete("/api/daily-reports/")
    assert resp.status_code == 200
    assert resp.json()["deleted_count"] == 2
    assert [r["section"] for r in dr_client.scope_store["daily_reports"]] == ["Mechanical"]


# ─── pachedu ─────────────────────────────────────────────────────────────────

def _pa_store():
    return {"pachedu_reports": [
        {"id": "p1", "section_choice": "Projects", "observer_name": "Pam",
         "dept": "Engineering", "status": "draft", "behaviour_type": "Intentional",
         "impacts": ["i1"], "checklist": ["c1"]},
        {"id": "p2", "section_choice": "Mechanical", "observer_name": "Max",
         "dept": "Mining", "status": "draft", "behaviour_type": "Unintentional",
         "impacts": [], "checklist": []},
    ]}


@pytest.fixture
def pa_client(monkeypatch):
    import app.routers.pachedu as mod
    return mount_router(monkeypatch, mod, "", _pa_store())


def _pa_payload(section="Mechanical"):
    return {"location": "Shaft", "date": "2026-10-06", "activityObserved": "Rigging",
            "whatDidYouSee": "Sling frayed", "behaviourType": "Intentional",
            "whatDidYouDo": "Stopped job", "sectionChoice": section}


def test_pachedu_scoped_list_is_predicated(pa_client):
    pa_client.as_user(SCOPED)
    resp = pa_client.get("/api/pachedu")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == ["p1"]
    assert ("eq", "section_choice", "Projects") in _predicates(pa_client)


def test_pachedu_scoped_suggestions_come_from_the_slice(pa_client):
    pa_client.as_user(SCOPED)
    assert pa_client.get("/api/pachedu/suggestions/departments").json() == ["Engineering"]


def test_pachedu_scoped_single_of_other_section_is_404(pa_client):
    pa_client.as_user(SCOPED)
    assert pa_client.get("/api/pachedu/p2").status_code == 404
    assert pa_client.get("/api/pachedu/p1").status_code == 200


def test_pachedu_scoped_create_is_stamped_projects(pa_client):
    pa_client.as_user(SCOPED)
    resp = pa_client.post("/api/pachedu", json=_pa_payload())
    assert resp.status_code == 200, resp.text
    assert pa_client.scope_store["pachedu_reports"][-1]["section_choice"] == "Projects"


def test_pachedu_projects_passes_section_validation(pa_client):
    pa_client.as_user(SCOPED)
    assert pa_client.post("/api/pachedu", json=_pa_payload("Projects")).status_code == 200


def test_pachedu_scoped_move_out_writes_nothing(pa_client):
    pa_client.as_user(SCOPED)
    resp = pa_client.patch("/api/pachedu/p1", json={"sectionChoice": "Mechanical"})
    assert resp.status_code == 404
    assert pa_client.scope_store["pachedu_reports"][0]["section_choice"] == "Projects"


def test_pachedu_scoped_stats_see_the_slice_only(pa_client):
    pa_client.as_user(SCOPED)
    body = pa_client.get("/api/pachedu/stats/overview").json()
    assert body["total"] == 1
    assert body["bySection"]["Projects"] == 1
    assert body["totalImpacts"] == 1


# ─── vfl ─────────────────────────────────────────────────────────────────────

def _vfl_store():
    return {
        "vfl_reports": [
            {"id": "v1", "section_choice": "Projects", "observer_name": "Pam",
             "status": "draft", "behaviour_category": "Safe Behaviour",
             "observation_type": "Safe Behaviour", "coaching_technique": "SBR"},
            {"id": "v2", "section_choice": "Mechanical", "observer_name": "Max",
             "status": "draft", "behaviour_category": "Unsafe Behaviour",
             "observation_type": "At Risk Behaviour", "coaching_technique": "CC"},
        ],
        "vfl_action_plan": [
            {"id": "va1", "report_id": "v1", "status": "Pending"},
            {"id": "va2", "report_id": "v2", "status": "Completed"},
        ],
    }


@pytest.fixture
def vfl_client(monkeypatch):
    import app.routers.vfl as mod
    return mount_router(monkeypatch, mod, "", _vfl_store())


def _vfl_payload(section="Mechanical"):
    return {"observerName": "Pam", "sectionChoice": section, "date": "2026-10-06",
            "time": "08:00", "behaviourCategory": "Safe Behaviour",
            "observationType": "Safe Behaviour",
            "description": "Good housekeeping seen.", "coachingTechnique": "SBR"}


def test_vfl_scoped_list_is_predicated(vfl_client):
    vfl_client.as_user(SCOPED)
    resp = vfl_client.get("/api/vfl")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["id"] for r in rows] == ["v1"]
    assert [a["id"] for a in rows[0]["actions"]] == ["va1"]
    assert ("eq", "section_choice", "Projects") in _predicates(vfl_client)


def test_vfl_scoped_suggestions_come_from_the_slice(vfl_client):
    vfl_client.as_user(SCOPED)
    assert vfl_client.get("/api/vfl/suggestions/observers").json() == ["Pam"]


def test_vfl_scoped_single_of_other_section_is_404(vfl_client):
    vfl_client.as_user(SCOPED)
    assert vfl_client.get("/api/vfl/v2").status_code == 404
    assert vfl_client.get("/api/vfl/v1").status_code == 200


def test_vfl_scoped_create_is_stamped_projects(vfl_client):
    vfl_client.as_user(SCOPED)
    resp = vfl_client.post("/api/vfl", json=_vfl_payload())
    assert resp.status_code == 200, resp.text
    assert vfl_client.scope_store["vfl_reports"][-1]["section_choice"] == "Projects"


def test_vfl_projects_passes_section_validation(vfl_client):
    vfl_client.as_user(SCOPED)
    assert vfl_client.post("/api/vfl", json=_vfl_payload("Projects")).status_code == 200


def test_vfl_scoped_move_out_writes_nothing(vfl_client):
    vfl_client.as_user(SCOPED)
    resp = vfl_client.patch("/api/vfl/v1", json={"sectionChoice": "Mechanical"})
    assert resp.status_code == 404
    assert vfl_client.scope_store["vfl_reports"][0]["section_choice"] == "Projects"


def test_vfl_scoped_stats_see_the_slice_only(vfl_client):
    vfl_client.as_user(SCOPED)
    body = vfl_client.get("/api/vfl/stats/overview").json()
    assert body["total"] == 1
    assert body["bySection"]["Projects"] == 1
    assert body["pendingActions"] == 1
    assert body["completedActions"] == 0


# ─── maintenance schedules (generated work orders inherit) ───────────────────

def _sch_store():
    return {
        "maintenance_schedules": [
            {"id": 1, "section": "Projects", "name": "Pump check",
             "recurrence_type": "monthly"},
            {"id": 2, "section": "Mechanical", "name": "Fan check",
             "recurrence_type": "monthly"},
        ],
        "maintenance_schedule_runs": [
            {"id": 11, "schedule_id": 1},
            {"id": 12, "schedule_id": 2},
        ],
    }


@pytest.fixture
def sch_client(monkeypatch):
    import app.routers.schedules as mod
    return mount_router(monkeypatch, mod, "/api/schedules", _sch_store())


def test_schedules_scoped_list_is_predicated(sch_client):
    sch_client.as_user(SCOPED)
    resp = sch_client.get("/api/schedules")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]
    assert ("eq", "section", "Projects") in _predicates(sch_client)


def test_schedules_scoped_manager_create_is_stamped_projects(sch_client):
    sch_client.as_user(SCOPED_MGR)
    resp = sch_client.post("/api/schedules", json={
        "name": "Belt check", "recurrence_type": "weekly"})
    assert resp.status_code == 200, resp.text
    assert sch_client.scope_store["maintenance_schedules"][-1]["section"] == "Projects"


def test_schedules_scoped_move_out_writes_nothing(sch_client):
    sch_client.as_user(SCOPED_MGR)
    resp = sch_client.patch("/api/schedules/1", json={"section": "Mechanical"})
    assert resp.status_code == 404
    assert sch_client.scope_store["maintenance_schedules"][0]["section"] == "Projects"


def test_schedules_scoped_runs_follow_the_parent(sch_client):
    sch_client.as_user(SCOPED)
    assert sch_client.get("/api/schedules/2/runs").status_code == 404
    ours = sch_client.get("/api/schedules/1/runs")
    assert [r["id"] for r in ours.json()] == [11]


class _GenOp:
    """Thenable stand-in for a PostgREST query: chain anything, execute once."""

    def __init__(self, table, payload=None, store=None, kind=None):
        self._table = table
        self._payload = payload
        self._store = store if store is not None else {}
        self._kind = kind

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        from types import SimpleNamespace

        body = self._payload
        if self._kind == "insert":
            if self._table == "work_orders":
                body = dict(body)
                body.setdefault("id", 777)
                self._store.setdefault("work_orders", []).append(body)
                return SimpleNamespace(data=[body])
            if self._table == "maintenance_schedule_runs":
                self._store.setdefault("runs", []).append(body)
            return SimpleNamespace(data=[body] if body is not None else [])
        return SimpleNamespace(data=[])


class _GenClient:
    def __init__(self, store):
        self._store = store

    def table(self, name):
        store = self._store

        class _T:
            def select(self, *a, **k):
                return _GenOp(name, store=store, kind="select")

            def insert(self, payload):
                return _GenOp(name, payload=payload, store=store, kind="insert")

            def update(self, payload):
                return _GenOp(name, payload=payload, store=store, kind="update")

            def delete(self):
                return _GenOp(name, store=store, kind="delete")

        return _T()


def test_schedule_generate_inherits_section_into_work_order(monkeypatch):
    """The cron path stamps the schedule's section onto the raised work order.

    Calls the real schedules._generate_one with a recording client: no HTTP,
    no user scope (the cron route is secret-auth), just the inheritance line.
    """
    from datetime import date as _date
    from app.routers import schedules

    store = {}
    monkeypatch.setattr(schedules, "supabase", _GenClient(store))
    sched = {"id": 9, "name": "Weekly pump check", "section": "Projects",
             "next_due_date": "2026-10-20", "recurrence_type": "weekly"}
    out = schedules._generate_one(sched, _date(2026, 10, 21))
    assert out and out["schedule_id"] == 9
    raised = store["work_orders"][0]
    assert raised["section"] == "Projects"
    assert raised["title"] == "Weekly pump check"


# ─── availability (inherits equipment) ───────────────────────────────────────

def _av_store():
    recent = (date.today() - timedelta(days=2)).isoformat()
    return {
        "equipment": [
            {"id": 1, "name": "Mill 1", "equipment_id": "M-1",
             "section": "Projects"},
            {"id": 2, "name": "Mill 2", "equipment_id": "M-2",
             "section": "Mechanical"},
        ],
        "availabilities": [
            {"id": 1, "equipment_id": 1, "date": recent,
             "operational_hours": 20.0, "breakdown_hours": 4.0,
             "availability_percentage": 83.3},
            {"id": 2, "equipment_id": 2, "date": recent,
             "operational_hours": 22.0, "breakdown_hours": 2.0,
             "availability_percentage": 91.7},
        ],
        "breakdowns": [
            {"machine_id": "M-1", "machine_name": "Mill 1",
             "breakdown_date": recent, "downtime_minutes": 60,
             "section": "Projects"},
            {"machine_id": "M-2", "machine_name": "Mill 2",
             "breakdown_date": recent, "downtime_minutes": 120,
             "section": "Mechanical"},
            {"machine_id": "M-9", "machine_name": "Ghost",
             "breakdown_date": recent, "downtime_minutes": 30,
             "section": "Mechanical"},
        ],
    }


@pytest.fixture
def av_client(monkeypatch):
    import app.routers.availability as mod
    return mount_router(monkeypatch, mod, "/api", _av_store())


def test_availability_scoped_list_sees_own_equipment(av_client):
    av_client.as_user(SCOPED)
    resp = av_client.get("/api/availabilities")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]
    assert ("eq", "section", "Projects") in _predicates(av_client)


def test_availability_scoped_stats_see_the_slice_only(av_client):
    av_client.as_user(SCOPED)
    assert av_client.get("/api/availabilities/stats").json()["totalEquipment"] == 1
    av_client.as_user(UNSCOPED)
    assert av_client.get("/api/availabilities/stats").json()["totalEquipment"] == 2


def test_availability_scoped_history_follows_equipment(av_client):
    av_client.as_user(SCOPED)
    assert av_client.get("/api/availabilities/history/2").status_code == 404
    assert len(av_client.get("/api/availabilities/history/1").json()) == 1


def test_availability_scoped_records_follow_equipment(av_client):
    av_client.as_user(SCOPED)
    resp = av_client.get("/api/availability-records")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]


def test_availability_from_breakdowns_scoped_drops_unmatched(av_client):
    av_client.as_user(SCOPED)
    rows = av_client.get("/api/availability-records/from-breakdowns").json()
    assert [r["equipment_id"] for r in rows] == [1]
    av_client.as_user(UNSCOPED)
    assert len(av_client.get("/api/availability-records/from-breakdowns").json()) == 3


def test_availability_scoped_create_to_other_equipment_writes_nothing(av_client):
    av_client.as_user(SCOPED)
    resp = av_client.post("/api/availability-records", json={
        "equipment_id": 2, "date": "2026-10-06",
        "operational_hours": 20.0, "breakdown_hours": 4.0,
        "availability_percentage": 83.3})
    assert resp.status_code == 404
    assert len(av_client.scope_store["availabilities"]) == 2


def test_availability_scoped_move_writes_nothing(av_client):
    av_client.as_user(SCOPED)
    resp = av_client.put("/api/availability-records/1", json={
        "equipment_id": 2, "date": "2026-10-06",
        "operational_hours": 20.0, "breakdown_hours": 4.0,
        "availability_percentage": 83.3})
    assert resp.status_code == 404
    assert av_client.scope_store["availabilities"][0]["equipment_id"] == 1


def test_availability_scoped_manager_cannot_delete_other_record(av_client):
    av_client.as_user(SCOPED_MGR)
    assert av_client.delete("/api/availability-records/2").status_code == 404
    assert len(av_client.scope_store["availabilities"]) == 2


# ─── compliance register (inherits equipment) ────────────────────────────────

def _co_store():
    return {
        "equipment": [
            {"id": 1, "name": "Mill 1", "section": "Projects"},
            {"id": 2, "name": "Mill 2", "section": "Mechanical"},
        ],
        "compliance_register": [
            {"id": 1, "equipment_id": "1", "equipment_name": "Mill 1",
             "inspection_type": "Pressure", "expiry_date": "2027-01-01"},
            {"id": 2, "equipment_id": "2", "equipment_name": "Mill 2",
             "inspection_type": "Pressure", "expiry_date": "2027-01-01"},
            {"id": 3, "equipment_id": None, "equipment_name": "Yard",
             "inspection_type": "General", "expiry_date": "2027-01-01"},
        ],
    }


@pytest.fixture
def co_client(monkeypatch):
    import app.routers.compliance as mod
    return mount_router(monkeypatch, mod, "/api/compliance", _co_store())


def test_compliance_scoped_list_follows_equipment(co_client):
    co_client.as_user(SCOPED)
    resp = co_client.get("/api/compliance")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]


def test_compliance_scoped_single_of_other_parent_is_404(co_client):
    co_client.as_user(SCOPED)
    assert co_client.get("/api/compliance/2").status_code == 404
    assert co_client.get("/api/compliance/3").status_code == 404
    assert co_client.get("/api/compliance/1").status_code == 200


def test_compliance_scoped_create_to_other_equipment_writes_nothing(co_client):
    co_client.as_user(SCOPED)
    resp = co_client.post("/api/compliance", json={
        "equipment_id": "2", "equipment_name": "Mill 2",
        "inspection_type": "Pressure", "expiry_date": "2027-06-01"})
    assert resp.status_code == 404
    assert len(co_client.scope_store["compliance_register"]) == 3


def test_compliance_scoped_unlinked_create_is_404(co_client):
    co_client.as_user(SCOPED)
    resp = co_client.post("/api/compliance", json={
        "equipment_name": "Yard", "inspection_type": "General",
        "expiry_date": "2027-06-01"})
    assert resp.status_code == 404
    co_client.as_user(UNSCOPED)
    assert co_client.post("/api/compliance", json={
        "equipment_name": "Yard", "inspection_type": "General",
        "expiry_date": "2027-06-01"}).status_code == 200


def test_compliance_scoped_patch_of_other_parent_is_404(co_client):
    # ComplianceUpdate carries no equipment_id, so a move is inexpressible;
    # the guard bites on touching an out-of-scope record instead.
    co_client.as_user(SCOPED)
    resp = co_client.patch("/api/compliance/2", json={"notes": "sneaky"})
    assert resp.status_code == 404
    assert co_client.scope_store["compliance_register"][1].get("notes") is None
    assert co_client.patch("/api/compliance/1", json={"notes": "ok"}).status_code == 200


# ─── condition monitoring (CrudRouter inherits) ──────────────────────────────

def _cm_store():
    return {
        "equipment": [
            {"id": 1, "name": "Mill 1", "section": "Projects"},
            {"id": 2, "name": "Mill 2", "section": "Mechanical"},
        ],
        "condition_monitoring": [
            {"id": 1, "equipment_id": "1", "equipment_name": "Mill 1",
             "monitoring_type": "oil", "sampled_date": "2026-10-01"},
            {"id": 2, "equipment_id": "2", "equipment_name": "Mill 2",
             "monitoring_type": "oil", "sampled_date": "2026-10-01"},
        ],
    }


@pytest.fixture
def cm_client(monkeypatch):
    import app.routers.condition_monitoring as mod
    return mount_router(monkeypatch, mod, "/api/condition-monitoring", _cm_store())


def test_condition_scoped_list_follows_equipment(cm_client):
    cm_client.as_user(SCOPED)
    resp = cm_client.get("/api/condition-monitoring")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]


def test_condition_scoped_patch_of_other_parent_is_404(cm_client):
    # CrudRouter exposes no GET-single; PATCH is the guarded single-row path.
    cm_client.as_user(SCOPED)
    resp = cm_client.patch("/api/condition-monitoring/2", json={"unit": "ppm"})
    assert resp.status_code == 404
    assert cm_client.scope_store["condition_monitoring"][1].get("unit") is None
    assert cm_client.patch("/api/condition-monitoring/1", json={"unit": "ppm"}).status_code == 200


def test_condition_scoped_create_to_other_equipment_writes_nothing(cm_client):
    cm_client.as_user(SCOPED)
    resp = cm_client.post("/api/condition-monitoring", json={
        "equipment_id": "2", "equipment_name": "Mill 2",
        "monitoring_type": "oil", "sampled_date": "2026-10-06"})
    assert resp.status_code == 404
    assert len(cm_client.scope_store["condition_monitoring"]) == 2
