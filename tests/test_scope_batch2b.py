# tests/test_scope_batch2b.py — Batch 2 people-module enforcement, part 2.
#
# Training, standby, duty roster, rotation covers, rotation singles,
# competency, NEC preview/apply, PPE order list, maintenance /ppe door:
# same contract as part 1 — scoped viewers get their slice, everything else
# is 404 (writing nothing), unscoped callers are untouched.

import pytest

from tests.scope_harness import (
    SCOPED, SCOPED_MGR, UNSCOPED, employees_store, mount_router,
)


def _base():
    return {"employees": employees_store()}


# ─── training ──────────────────────────────────────────────────────────────

def _cert(i, emp, name):
    return {"id": i, "employee_id": emp, "employee_name": name, "department": "Eng",
            "certification_name": "First Aid", "expiry_date": "2027-01-01",
            "required_refresher": "Annual", "certificate_url": None}


def _training_store():
    store = _base()
    store["training_certifications"] = [_cert("t1", "C1001", "Pam Moyo"),
                                        _cert("t2", "C2001", "Mike Doe")]
    return store


@pytest.fixture
def training_client(monkeypatch):
    import app.routers.training as mod
    return mount_router(monkeypatch, mod, "/api/training", _training_store())


def _cert_form(emp, name):
    return {"employee_id": emp, "employee_name": name, "department": "Eng",
            "certification_name": "First Aid", "expiry_date": "2027-01-01",
            "required_refresher": "Annual"}


def test_training_scoped_list_and_single_are_sliced(training_client):
    training_client.as_user(SCOPED)
    resp = training_client.get("/api/training/")
    assert [r["id"] for r in resp.json()] == ["t1"]
    assert training_client.get("/api/training/t2").status_code == 404
    assert training_client.get("/api/training/t1").status_code == 200


def test_training_scoped_create_and_move_are_404(training_client):
    training_client.as_user(SCOPED)
    resp = training_client.post("/api/training/", data=_cert_form("C2001", "Mike Doe"))
    assert resp.status_code == 404
    assert len(training_client.scope_store["training_certifications"]) == 2
    resp = training_client.put("/api/training/t1", data={"employee_id": "C2001"})
    assert resp.status_code == 404
    assert training_client.scope_store["training_certifications"][0]["employee_id"] == "C1001"


def test_training_unscoped_list_is_whole(training_client):
    training_client.as_user(UNSCOPED)
    assert [r["id"] for r in training_client.get("/api/training/").json()] == ["t1", "t2"]


# ─── standby ───────────────────────────────────────────────────────────────

def _standby_store():
    store = _base()
    store["standby_schedules"] = [
        {"id": 1, "employee_id": "C1001", "employee_name": "Pam Moyo",
         "shift_type": "10-4", "on_days": 10, "off_days": 4,
         "cycle_start_date": "2026-10-01", "is_active": True},
        {"id": 2, "employee_id": "C2001", "employee_name": "Mike Doe",
         "shift_type": "10-4", "on_days": 10, "off_days": 4,
         "cycle_start_date": "2026-10-01", "is_active": True},
    ]
    return store


@pytest.fixture
def standby_client(monkeypatch):
    import app.routers.standby as mod
    return mount_router(monkeypatch, mod, "/api/standby", _standby_store())


def test_standby_scoped_list_and_single_are_sliced(standby_client):
    standby_client.as_user(SCOPED)
    assert [r["id"] for r in standby_client.get("/api/standby").json()] == [1]
    assert standby_client.get("/api/standby/2").status_code == 404
    assert standby_client.get("/api/standby/1").status_code == 200


def test_standby_scoped_create_and_move_are_404(standby_client):
    standby_client.as_user(SCOPED)
    resp = standby_client.post("/api/standby", json={
        "employee_id": "C2001", "employee_name": "Mike Doe", "cycle_start_date": "2026-10-01"})
    assert resp.status_code == 404
    assert len(standby_client.scope_store["standby_schedules"]) == 2
    resp = standby_client.put("/api/standby/1", json={"employee_id": "C2001"})
    assert resp.status_code == 404
    assert standby_client.scope_store["standby_schedules"][0]["employee_id"] == "C1001"


def test_standby_unscoped_list_is_whole(standby_client):
    standby_client.as_user(UNSCOPED)
    # created_at/id both descending, NULLs tie: newest id first.
    assert [r["id"] for r in standby_client.get("/api/standby").json()] == [2, 1]


# ─── duty roster ───────────────────────────────────────────────────────────

def _duty_store():
    store = _base()
    store["duty_roster"] = [
        {"id": 1, "employee_id": "C1001", "employee_name": "Pam Moyo",
         "department": "Ops", "date_from": "2026-11-01", "date_to": "2026-11-02"},
        {"id": 2, "employee_id": "C2001", "employee_name": "Mike Doe",
         "department": "Ops", "date_from": "2026-11-03", "date_to": "2026-11-07"},
    ]
    return store


@pytest.fixture
def duty_client(monkeypatch):
    import app.routers.duty_roster as mod
    return mount_router(monkeypatch, mod, "/api/duty-roster", _duty_store())


def test_duty_scoped_list_single_create_are_sliced(duty_client):
    duty_client.as_user(SCOPED)
    assert [r["id"] for r in duty_client.get("/api/duty-roster").json()] == [1]
    assert duty_client.get("/api/duty-roster/2").status_code == 404
    resp = duty_client.post("/api/duty-roster", json={
        "employee_id": "C2001", "employee_name": "Mike Doe",
        "date_from": "2026-12-01", "date_to": "2026-12-02"})
    assert resp.status_code == 404
    assert len(duty_client.scope_store["duty_roster"]) == 2


def test_duty_scoped_overlap_ignores_hidden_rows(duty_client):
    duty_client.as_user(SCOPED)
    # Overlaps the hidden Mechanical entry (Ops, Nov 3–7) — no 409, because a
    # 409 would name the hidden holder.
    resp = duty_client.post("/api/duty-roster", json={
        "employee_id": "C1001", "employee_name": "Pam Moyo", "department": "Ops",
        "date_from": "2026-11-04", "date_to": "2026-11-05"})
    assert resp.status_code == 200, resp.text


def test_duty_unscoped_overlap_still_clashes(duty_client):
    duty_client.as_user(UNSCOPED)
    resp = duty_client.post("/api/duty-roster", json={
        "employee_id": "C1001", "employee_name": "Pam Moyo", "department": "Ops",
        "date_from": "2026-11-04", "date_to": "2026-11-05"})
    assert resp.status_code == 409


# ─── rotation covers ───────────────────────────────────────────────────────

def _covers_store():
    store = _base()
    store["rotation_covers"] = [
        {"id": 1, "kind": "standby", "rotation_id": 1,
         "absent_employee_id": "C1001", "absent_employee_name": "Pam Moyo",
         "cover_employee_id": "C2001", "cover_employee_name": "Mike Doe",
         "date_from": "2026-11-01", "date_to": "2026-11-07"},
        {"id": 2, "kind": "standby", "rotation_id": 1,
         "absent_employee_id": "C2001", "absent_employee_name": "Mike Doe",
         "cover_employee_id": "C2999", "cover_employee_name": "Stranger X",
         "date_from": "2026-11-01", "date_to": "2026-11-07"},
    ]
    return store


@pytest.fixture
def covers_client(monkeypatch):
    import app.routers.rotation_covers as mod
    return mount_router(monkeypatch, mod, "/api/rotation-covers", _covers_store())


def test_covers_scoped_list_and_single_need_any_side_in_scope(covers_client):
    covers_client.as_user(SCOPED)
    assert [r["id"] for r in covers_client.get("/api/rotation-covers").json()] == [1]
    assert covers_client.get("/api/rotation-covers/2").status_code == 404


def test_covers_scoped_create_for_invisible_pair_is_404(covers_client):
    covers_client.as_user(SCOPED)
    resp = covers_client.post("/api/rotation-covers", json={
        "kind": "standby", "rotation_id": 1,
        "absent_employee_id": "C2001", "absent_employee_name": "Mike Doe",
        "cover_employee_id": "C2999", "cover_employee_name": "Stranger X",
        "date_from": "2026-12-01", "date_to": "2026-12-07"})
    assert resp.status_code == 404
    assert len(covers_client.scope_store["rotation_covers"]) == 2


def test_covers_unscoped_list_is_whole(covers_client):
    covers_client.as_user(UNSCOPED)
    assert [r["id"] for r in covers_client.get("/api/rotation-covers").json()] == [1, 2]


# ─── rotation singles (hand-rolled get_one beside CrudRouter) ───────────────

def _rotation_store():
    store = _base()
    store["standby_rotations"] = [
        {"id": 1, "name": "R1", "section": "Projects",
         "members": [{"employee_id": "C2001", "crew": [{"employee_id": "C1001"}]}]},
        {"id": 2, "name": "R2", "section": "Mechanical",
         "members": [{"employee_id": "C2001", "crew": []}]},
    ]
    store["duty_rotations"] = [
        {"id": 1, "name": "D1", "members": [{"employee_id": "C1001"}]},
        {"id": 2, "name": "D2", "members": [{"employee_id": "C2001"}]},
    ]
    return store


@pytest.fixture
def rotations_client(monkeypatch):
    import app.routers.standby_rotations as sbr
    import app.routers.duty_rotations as dr
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.auth import get_current_user as real_gcu
    from tests.scope_harness import FakeSupabase

    store = _rotation_store()
    current = [UNSCOPED]
    monkeypatch.setattr(sbr, "supabase", FakeSupabase(store, []))
    monkeypatch.setattr(dr, "supabase", FakeSupabase(store, []))

    async def _override():
        return current[0]

    app = FastAPI()
    app.include_router(sbr.router, prefix="/api/standby-rotations")
    app.include_router(dr.router, prefix="/api/duty-rotations")
    app.dependency_overrides[real_gcu] = _override
    client = TestClient(app, raise_server_exceptions=False)
    client.as_user = lambda user: current.__setitem__(0, user)  # noqa: E731
    return client


def test_standby_rotation_single_section_or_crew_member_is_visible(rotations_client):
    rotations_client.as_user(SCOPED)
    assert rotations_client.get("/api/standby-rotations/1").status_code == 200
    assert rotations_client.get("/api/standby-rotations/2").status_code == 404


def test_duty_rotation_single_needs_a_member_in_scope(rotations_client):
    rotations_client.as_user(SCOPED)
    assert rotations_client.get("/api/duty-rotations/1").status_code == 200
    assert rotations_client.get("/api/duty-rotations/2").status_code == 404


def test_rotation_singles_unscoped_are_whole(rotations_client):
    rotations_client.as_user(UNSCOPED)
    assert rotations_client.get("/api/standby-rotations/2").status_code == 200
    assert rotations_client.get("/api/duty-rotations/2").status_code == 200


# ─── competency (CrudRouter opt-in smoke) ──────────────────────────────────

def _comp_store():
    store = _base()
    store["competency_matrix"] = [
        {"id": 1, "employee_id": "C1001", "employee_name": "Pam Moyo"},
        {"id": 2, "employee_id": "C2001", "employee_name": "Mike Doe"},
    ]
    return store


@pytest.fixture
def comp_client(monkeypatch):
    import app.routers.competency as mod
    return mount_router(monkeypatch, mod, "/api/competency", _comp_store())


def test_competency_scoped_list_is_sliced(comp_client):
    comp_client.as_user(SCOPED)
    assert [r["id"] for r in comp_client.get("/api/competency").json()] == [1]


def test_competency_unscoped_list_is_whole(comp_client):
    comp_client.as_user(UNSCOPED)
    # order_by employee_name: Mike Doe before Pam Moyo.
    assert [r["id"] for r in comp_client.get("/api/competency").json()] == [2, 1]


# ─── NEC preview filter + apply guard ──────────────────────────────────────

def _preview():
    return {
        "period": {"start": "2026-10-01", "end": "2026-10-31"},
        "line_items": [
            {"employee_id": 1, "human_code": "C1001", "kind": "create"},
            {"employee_id": 2, "human_code": "C2001", "kind": "update"},
            {"employee_id": None, "human_code": None, "kind": "unresolved"},
        ],
        "sheet_summaries": [
            {"database_id": 1, "human_code": "C1001", "name": "Pam Moyo"},
            {"database_id": 2, "human_code": "C2001", "name": "Mike Doe"},
        ],
        "missing_sheets": [
            {"database_id": 1, "human_code": "C1001", "name": "Pam Moyo"},
            {"database_id": 2, "human_code": "C2001", "name": "Mike Doe"},
        ],
        "duplicate_sheet_groups": [
            {"key": "C1001", "sheet_ids": ["s1", "s2"]},
            {"key": "C2001", "sheet_ids": ["s3", "s4"]},
        ],
        "stats": {"create": 1, "update": 1, "unchanged": 0, "unresolved": 1, "exception": 0},
    }


def _bank():
    return {"register": {"C1001"}, "db": {1}, "names": {"pam moyo"}}


def test_nec_preview_filter_keeps_the_slice_and_restats():
    import app.routers.nec_timesheet_import as mod

    out = mod._filter_preview_for_scope(_preview(), _bank())
    assert [x["human_code"] for x in out["line_items"]] == ["C1001"]
    assert [s["database_id"] for s in out["sheet_summaries"]] == [1]
    assert [m["database_id"] for m in out["missing_sheets"]] == [1]
    assert [g["key"] for g in out["duplicate_sheet_groups"]] == ["C1001"]
    assert out["stats"] == {"create": 1, "update": 0, "unchanged": 0, "unresolved": 0, "exception": 0}


def test_nec_preview_filter_matches_names_and_codes():
    import app.routers.nec_timesheet_import as mod

    preview = _preview()
    preview["line_items"] = [
        {"employee_id": 9, "human_code": "c1001", "kind": "create"},   # case differs
        {"employee_id": None, "human_code": None, "name": "Pam  Moyo", "kind": "create"},
        {"employee_id": None, "human_code": None, "name": "Nobody Knows", "kind": "create"},
    ]
    out = mod._filter_preview_for_scope(preview, _bank())
    assert len(out["line_items"]) == 2


@pytest.fixture
def nec_client(monkeypatch):
    import app.routers.nec_timesheet_import as mod

    meta = {"period_start": "2026-10-01", "period_end": "2026-10-31", "status": "review_ready"}
    monkeypatch.setattr(mod.job_store, "load_job", lambda job_id: dict(meta))
    monkeypatch.setattr(mod.job_store, "load_review_json", lambda job_id: {"sheets": []})
    monkeypatch.setattr(mod.job_store, "_write_meta", lambda job_id, m: None)
    monkeypatch.setattr(mod, "build_preview", lambda review, s, e: _preview())
    return mount_router(monkeypatch, mod, "/api/nec-timesheet-import", _base())


def test_nec_preview_wiring_filters_for_scoped_only(nec_client):
    nec_client.as_user(SCOPED)
    resp = nec_client.get("/api/nec-timesheet-import/jobs/j1/preview")
    assert resp.status_code == 200
    assert [x["human_code"] for x in resp.json()["preview"]["line_items"]] == ["C1001"]
    nec_client.as_user(UNSCOPED)
    resp = nec_client.get("/api/nec-timesheet-import/jobs/j1/preview")
    assert len(resp.json()["preview"]["line_items"]) == 3


def test_nec_apply_is_refused_for_scoped_managers(nec_client):
    nec_client.as_user(SCOPED_MGR)
    resp = nec_client.post("/api/nec-timesheet-import/jobs/j1/apply?dry_run=true")
    assert resp.status_code == 403


# ─── PPE order list ────────────────────────────────────────────────────────

def _order_row(record_id, emp):
    return {"record_id": record_id, "added_by": "a@x",
            "entry": {"record_id": record_id, "employee_id": emp, "kind": "line"}}


def _order_store():
    store = _base()
    store["ppe_order_list"] = [_order_row("r1", "C1001"), _order_row("r2", "C2001"),
                               _order_row("r3", "")]
    return store


@pytest.fixture
def order_client(monkeypatch):
    import app.routers.ppe_order_list as mod
    return mount_router(monkeypatch, mod, "/api/ppe-order-list", _order_store())


def test_order_list_scoped_sees_own_lines_only(order_client):
    order_client.as_user(SCOPED)
    assert [e["record_id"] for e in order_client.get("/api/ppe-order-list").json()] == ["r1"]


def test_order_list_scoped_add_refuses_the_whole_batch(order_client):
    order_client.as_user(SCOPED)
    resp = order_client.post("/api/ppe-order-list", json={"entries": [
        {"record_id": "101", "employee_id": "C1001"},
        {"record_id": "102", "employee_id": "C2001"},
    ]})
    assert resp.status_code == 404
    assert len(order_client.scope_store["ppe_order_list"]) == 3


def test_order_list_scoped_patch_of_hidden_line_is_404(order_client):
    order_client.as_user(SCOPED)
    resp = order_client.patch("/api/ppe-order-list/r2", json={"qty": 5})
    assert resp.status_code == 404


def test_order_list_scoped_remove_and_clear_keep_hidden_lines(order_client):
    order_client.as_user(SCOPED)
    resp = order_client.post("/api/ppe-order-list/remove", json={"record_ids": ["r1", "r2"]})
    assert resp.json() == {"ok": True}
    assert sorted(r["record_id"] for r in order_client.scope_store["ppe_order_list"]) == ["r2", "r3"]
    resp = order_client.delete("/api/ppe-order-list")
    assert resp.json() == {"ok": True}
    assert sorted(r["record_id"] for r in order_client.scope_store["ppe_order_list"]) == ["r2", "r3"]


def test_order_list_unscoped_is_whole(order_client):
    order_client.as_user(UNSCOPED)
    assert len(order_client.get("/api/ppe-order-list").json()) == 3


# ─── maintenance /ppe second door ─────────────────────────────────────────

def _maint_store():
    store = _base()
    store["ppe_records"] = [
        {"id": 1, "employee_id": "C1001", "ppe_type": "Boots"},
        {"id": 2, "employee_id": "C2001", "ppe_type": "Boots"},
    ]
    return store


@pytest.fixture
def maint_client(monkeypatch):
    import app.routers.maintenance as mod
    return mount_router(monkeypatch, mod, "/api/maintenance", _maint_store())


def test_maintenance_ppe_door_is_scoped(maint_client):
    maint_client.as_user(SCOPED)
    assert [r["id"] for r in maint_client.get("/api/maintenance/ppe").json()] == [1]
    resp = maint_client.post("/api/maintenance/ppe", json={
        "employee_name": "Mike Doe", "employee_id": "C2001", "department": "Eng",
        "position": "Fitter", "ppe_type": "Boots", "item_name": "Boot X",
        "issue_date": "2026-10-01"})
    assert resp.status_code == 404
    assert len(maint_client.scope_store["ppe_records"]) == 2


def test_maintenance_ppe_door_unscoped_is_whole(maint_client):
    maint_client.as_user(UNSCOPED)
    assert [r["id"] for r in maint_client.get("/api/maintenance/ppe").json()] == [1, 2]
