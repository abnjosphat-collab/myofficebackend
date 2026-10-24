# tests/test_scope_batch2a.py — Batch 2 people-module enforcement, part 1.
#
# Overtime, NEC timesheets, artisan timesheets, PPE, leaves, employees: a
# scoped viewer lists their slice, reads/writes anything else as 404 (writing
# nothing), and can never move a row across the boundary. Unscoped callers run
# the exact queries they always did.

import pytest

from tests.scope_harness import (
    SCOPED, SCOPED_MGR, UNSCOPED, employees_store, mount_router,
)


def _base():
    return {"employees": employees_store()}


def _predicates(client):
    return [c for c in client.scope_calls if c[0] in ("eq", "in")]


# ─── overtime ──────────────────────────────────────────────────────────────

def _ot_store():
    store = _base()
    store["overtime"] = [
        {"id": 1, "employee_id": "C1001", "status": "pending", "date": "2026-10-01"},
        {"id": 2, "employee_id": "C2001", "status": "pending", "date": "2026-10-02"},
    ]
    return store


@pytest.fixture
def ot_client(monkeypatch):
    import app.routers.overtime as mod
    return mount_router(monkeypatch, mod, "/api/overtime", _ot_store())


def test_overtime_scoped_list_is_predicated(ot_client):
    ot_client.as_user(SCOPED)
    resp = ot_client.get("/api/overtime")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [1]
    assert ("in", "employee_id", ["C1001"]) in _predicates(ot_client)


def test_overtime_unscoped_list_query_is_unchanged(ot_client):
    ot_client.as_user(UNSCOPED)
    resp = ot_client.get("/api/overtime")
    assert [r["id"] for r in resp.json()] == [1, 2]
    assert _predicates(ot_client) == []


def test_overtime_scoped_single_of_other_section_is_404(ot_client):
    ot_client.as_user(SCOPED)
    assert ot_client.get("/api/overtime/2").status_code == 404
    assert ot_client.get("/api/overtime/1").status_code == 200


def test_overtime_scoped_create_for_other_section_is_404(ot_client):
    ot_client.as_user(SCOPED)
    resp = ot_client.post("/api/overtime", json={
        "employee_name": "Mike Doe", "employee_id": "C2001", "position": "Fitter",
        "overtime_type": "normal", "date": "2026-10-05", "hours": 2})
    assert resp.status_code == 404
    assert len(ot_client.scope_store["overtime"]) == 2


def test_overtime_scoped_move_to_other_person_writes_nothing(ot_client):
    ot_client.as_user(SCOPED)
    resp = ot_client.patch("/api/overtime/1", json={"employee_id": "C2001"})
    assert resp.status_code == 404
    assert ot_client.scope_store["overtime"][0]["employee_id"] == "C1001"


def test_overtime_scoped_bulk_touches_visible_rows_only(ot_client):
    ot_client.as_user(SCOPED_MGR)
    resp = ot_client.post("/api/overtime/bulk-status", json={"ids": [1, 2], "status": "approved"})
    assert resp.status_code == 200
    assert (resp.json()["succeeded"], resp.json()["failed"]) == (1, 1)
    assert ot_client.scope_store["overtime"][1]["status"] == "pending"


def test_overtime_unscoped_bulk_is_whole(ot_client):
    ot_client.as_user(UNSCOPED)
    resp = ot_client.post("/api/overtime/bulk-status", json={"ids": [1, 2], "status": "approved"})
    assert (resp.json()["succeeded"], resp.json()["failed"]) == (2, 0)


# ─── NEC timesheets (db ids) ───────────────────────────────────────────────

def _ts_store():
    store = _base()
    store["timesheets"] = [
        {"id": 1, "employee_id": 1, "date": "2026-10-01"},
        {"id": 2, "employee_id": 2, "date": "2026-10-02"},
    ]
    return store


@pytest.fixture
def ts_client(monkeypatch):
    import app.routers.timesheets as mod
    return mount_router(monkeypatch, mod, "/api/timesheets", _ts_store())


def test_timesheets_scoped_list_uses_db_ids(ts_client):
    ts_client.as_user(SCOPED)
    resp = ts_client.get("/api/timesheets")
    assert [r["id"] for r in resp.json()] == [1]
    assert ("in", "employee_id", [1]) in _predicates(ts_client)


def test_timesheets_scoped_single_and_create_are_404(ts_client):
    ts_client.as_user(SCOPED)
    assert ts_client.get("/api/timesheets/2").status_code == 404
    resp = ts_client.post("/api/timesheets", json={"employee_id": 2, "date": "2026-10-06"})
    assert resp.status_code == 404
    assert len(ts_client.scope_store["timesheets"]) == 2


def test_timesheets_unscoped_list_query_is_unchanged(ts_client):
    ts_client.as_user(UNSCOPED)
    assert [r["id"] for r in ts_client.get("/api/timesheets").json()] == [1, 2]
    assert _predicates(ts_client) == []


# ─── artisan timesheets (dual links) ─────────────────────────────────────────

def _art_store():
    store = _base()
    store["artisan_timesheets"] = [
        {"id": 1, "employee_db_id": 1, "employee_id": "C1001", "year": 2026, "month": 10},
        {"id": 2, "employee_db_id": 2, "employee_id": "C2001", "year": 2026, "month": 10},
        {"id": 3, "employee_db_id": None, "employee_id": "C1001", "year": 2026, "month": 9},
    ]
    return store


@pytest.fixture
def art_client(monkeypatch):
    import app.routers.artisan_timesheets as mod
    return mount_router(monkeypatch, mod, "/api/artisan-timesheets", _art_store())


def test_artisan_scoped_list_matches_either_link(art_client):
    art_client.as_user(SCOPED)
    resp = art_client.get("/api/artisan-timesheets")
    assert [r["id"] for r in resp.json()] == [1, 3]
    ors = [c[1] for c in art_client.scope_calls if c[0] == "or_"]
    assert ors == ['employee_db_id.in.(1),employee_id.in.("C1001")']


def test_artisan_scoped_single_create_move_are_404(art_client):
    art_client.as_user(SCOPED)
    assert art_client.get("/api/artisan-timesheets/2").status_code == 404
    resp = art_client.post("/api/artisan-timesheets", json={
        "employee_id": "C2001", "employee_db_id": 2, "employee_name": "Mike Doe",
        "year": 2026, "month": 10})
    assert resp.status_code == 404
    assert len(art_client.scope_store["artisan_timesheets"]) == 3
    resp = art_client.patch("/api/artisan-timesheets/1",
                            json={"employee_id": "C2001", "employee_db_id": 2})
    assert resp.status_code == 404
    assert art_client.scope_store["artisan_timesheets"][0]["employee_id"] == "C1001"


def test_artisan_unscoped_list_has_no_predicate(art_client):
    art_client.as_user(UNSCOPED)
    assert [r["id"] for r in art_client.get("/api/artisan-timesheets").json()] == [1, 2, 3]
    assert [c for c in art_client.scope_calls if c[0] == "or_"] == []


# ─── PPE ───────────────────────────────────────────────────────────────────

def _ppe_store():
    store = _base()
    store["ppe_records"] = [
        {"id": 1, "employee_id": "C1001", "ppe_type": "Boots", "status": "active"},
        {"id": 2, "employee_id": "C2001", "ppe_type": "Boots", "status": "active"},
    ]
    return store


@pytest.fixture
def ppe_client(monkeypatch):
    import app.routers.ppe as mod
    return mount_router(monkeypatch, mod, "/api/ppe", _ppe_store())


def test_ppe_scoped_list_single_and_stats_are_sliced(ppe_client):
    ppe_client.as_user(SCOPED)
    assert [r["id"] for r in ppe_client.get("/api/ppe").json()] == [1]
    assert ppe_client.get("/api/ppe/2").status_code == 404
    assert ppe_client.get("/api/ppe/employee/C2001").json() == []
    stats = ppe_client.get("/api/ppe/stats/summary")
    assert stats.status_code == 200
    assert stats.json()["total_records"] == 1


def test_ppe_scoped_create_for_registered_outsider_is_404_not_422(ppe_client):
    ppe_client.as_user(SCOPED)
    resp = ppe_client.post("/api/ppe", json={
        "employee_id": "C2001", "ppe_type": "Boots", "item_name": "Boot X",
        "issue_date": "2026-10-01"})
    assert resp.status_code == 404
    assert len(ppe_client.scope_store["ppe_records"]) == 2


def test_ppe_scoped_move_writes_nothing(ppe_client):
    ppe_client.as_user(SCOPED)
    resp = ppe_client.patch("/api/ppe/1", json={"employee_id": "C2001"})
    assert resp.status_code == 404
    assert ppe_client.scope_store["ppe_records"][0]["employee_id"] == "C1001"


def test_ppe_unscoped_list_and_stats_are_whole(ppe_client):
    ppe_client.as_user(UNSCOPED)
    # created_at/id both descending, NULLs tie: newest id first.
    assert [r["id"] for r in ppe_client.get("/api/ppe").json()] == [2, 1]
    assert ppe_client.get("/api/ppe/stats/summary").json()["total_records"] == 2


# ─── leaves ────────────────────────────────────────────────────────────────

def _leave_row(i, emp, name):
    return {"id": i, "employee_id": emp, "employee_name": name, "position": "Fitter",
            "contact_number": "1", "leave_type": "annual",
            "start_date": "2026-11-01", "end_date": "2026-11-03", "total_days": 3,
            "reason": "Rest", "emergency_contact": None, "handover_to": None,
            "status": "pending", "applied_date": "2026-10-20T00:00:00",
            "updated_at": None, "notes": None}


def _leave_store():
    store = _base()
    store["leaves"] = [_leave_row(11, "C1001", "Pam Moyo"), _leave_row(12, "C2001", "Mike Doe")]
    return store


@pytest.fixture
def leave_client(monkeypatch):
    import app.routers.leaves as mod
    return mount_router(monkeypatch, mod, "/api/leaves", _leave_store())


def test_leaves_scoped_single_and_update_are_404(leave_client):
    leave_client.as_user(SCOPED)
    assert leave_client.get("/api/leaves/12").status_code == 404
    # Approve/reject is manager-gated on the body's status value (row-independent,
    # so no leak); a scoped manager reaching the row itself gets 404.
    assert leave_client.patch("/api/leaves/12", json={"status": "approved"}).status_code == 403
    leave_client.as_user(SCOPED_MGR)
    assert leave_client.patch("/api/leaves/12", json={"status": "approved"}).status_code == 404
    assert leave_client.scope_store["leaves"][1]["status"] == "pending"


def test_leaves_scoped_create_is_404_not_409(leave_client):
    leave_client.as_user(SCOPED)
    resp = leave_client.post("/api/leaves", json={
        "employee_id": "C2001", "employee_name": "Mike Doe", "position": "Fitter",
        "contact_number": "07", "leave_type": "annual",
        "start_date": "2026-11-02", "end_date": "2026-11-04", "reason": "x"})
    assert resp.status_code == 404
    assert len(leave_client.scope_store["leaves"]) == 2


def test_leaves_scoped_bulk_touches_visible_rows_only(leave_client):
    leave_client.as_user(SCOPED_MGR)
    resp = leave_client.post("/api/leaves/bulk-status", json={"ids": [11, 12], "status": "approved"})
    assert (resp.json()["succeeded"], resp.json()["failed"]) == (1, 1)
    assert leave_client.scope_store["leaves"][1]["status"] == "pending"


def test_leaves_unscoped_single_is_200(leave_client):
    leave_client.as_user(UNSCOPED)
    assert leave_client.get("/api/leaves/12").status_code == 200


# ─── employees ─────────────────────────────────────────────────────────────

def _emp_body(section):
    return {"employee_id": "C1001", "first_name": "Pam", "last_name": "Moyo",
            "id_number": "ID1", "date_of_engagement": "2020-01-01",
            "designation": "Fitter", "section": section}


@pytest.fixture
def emp_client(monkeypatch):
    import app.routers.employees as mod
    return mount_router(monkeypatch, mod, "/api/employees", _base())


def test_employees_scoped_section_move_is_refused(emp_client):
    emp_client.as_user(SCOPED)
    resp = emp_client.put("/api/employees/1", json=_emp_body("Mechanical"))
    assert resp.status_code == 404
    assert emp_client.scope_store["employees"][0]["section"] == "Projects"


def test_employees_scoped_update_of_outsider_is_404(emp_client):
    emp_client.as_user(SCOPED)
    body = _emp_body("Mechanical")
    body["employee_id"] = "C2001"
    assert emp_client.put("/api/employees/2", json=body).status_code == 404


def test_employees_scoped_delete_of_outsider_is_404_and_keeps(emp_client):
    emp_client.as_user(SCOPED_MGR)
    assert emp_client.delete("/api/employees/2").status_code == 404
    assert len(emp_client.scope_store["employees"]) == 2


def test_employees_scoped_bulk_normalize_counts_hidden_as_failed(emp_client):
    emp_client.as_user(SCOPED_MGR)
    resp = emp_client.post("/api/employees/bulk-normalize", json={"updates": [
        {"id": 1, "designation": "Fitter Class 1"},
        {"id": 2, "designation": "Fitter Class 1"},
    ]})
    assert resp.status_code == 200
    assert (resp.json()["succeeded"], resp.json()["failed"]) == (1, 1)
    assert emp_client.scope_store["employees"][1]["designation"] == "Fitter"


def test_employees_unscoped_update_is_whole(emp_client):
    emp_client.as_user(UNSCOPED)
    resp = emp_client.put("/api/employees/2", json={
        "employee_id": "C2001", "first_name": "Mike", "last_name": "Doe",
        "id_number": "ID2", "date_of_engagement": "2020-01-01",
        "designation": "Fitter", "section": "Mechanical"})
    assert resp.status_code == 200
