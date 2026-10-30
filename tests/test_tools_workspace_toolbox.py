"""Monthly toolbox checks: set content lines and per-person monthly verification."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.rate_limit import limiter
from app.routers import tools_workspace
from main import app


@pytest.fixture(autouse=True)
def disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


client = TestClient(app)


def setup_function():
    tools_workspace._reset_for_tests()


def register(username, can_issue=False):
    response = client.post("/api/tools-workspace/auth/register", json={"name": username.title(), "username": username, "password": "secret12", "can_issue": can_issue})
    assert response.status_code == 201
    return response.json()["token"]


def admin_and_issuer():
    admin = register("admin")
    viewer = register("issuer")
    accounts = client.get("/api/tools-workspace/accounts", headers={"Authorization": f"Bearer {admin}"}).json()
    target = next(account for account in accounts if account["role"] == "viewer")
    promoted = client.patch(f"/api/tools-workspace/accounts/{target['id']}", headers={"Authorization": f"Bearer {admin}"}, json={"role": "issuer", "department": "Engineering"})
    assert promoted.status_code == 200
    return admin, viewer


def make_employee(auth, number, name="Alf"):
    employee = client.post("/api/tools-workspace/employees", headers=auth, json={"employee_number": number, "name": name, "department": "Engineering"}).json()
    assert "id" in employee
    return employee


def make_tool(auth, register_number, name="Spanner"):
    tool = client.post("/api/tools-workspace/tools", headers=auth, json={"register_number": register_number, "name": name, "storage_location": "Workshop"}).json()
    assert "id" in tool
    return tool


def make_eligible(auth, employee, tool):
    assert client.post("/api/tools-workspace/competencies", headers=auth, json={"employee_id": employee["id"], "tool_id": tool["id"], "trained": True, "qualified": True, "authorized": True}).status_code == 201


def make_set(auth, head, members=(), lines=()):
    # Member linking through the API is retired (POST answers 410); legacy
    # links are rows in the table, so tests build them directly.
    for member in members:
        tools_workspace._insert("set_members", {"id": str(uuid.uuid4()), "set_tool_id": head["id"], "member_tool_id": member["id"], "qty": 1, "created_by": "tests", "created_at": tools_workspace._now()})
    for name, size, qty in lines:
        assert client.post(f"/api/tools-workspace/sets/{head['id']}/lines", headers=auth, json={"name": name, "size": size, "qty": qty}).status_code == 201


def issue_set(auth, head, employee):
    response = client.post(f"/api/tools-workspace/tools/{head['id']}/issue", headers=auth, json={"employee_id": employee["id"], "location": "Pit"})
    assert response.status_code == 200
    return response.json()


def test_lines_crud_and_guards():
    admin, token = admin_and_issuer()
    aa = {"Authorization": f"Bearer {admin}"}
    head = make_tool(aa, "SET-1", "Gedore set")
    member = make_tool(aa, "T-1", "Spanner")
    make_set(aa, head, [member], [("Spanner", "14", 2)])
    contents = client.get(f"/api/tools-workspace/sets/{head['id']}/contents", headers=aa).json()
    assert [line["name"] for line in contents["lines"]] == ["Spanner"]
    assert contents["lines"][0]["qty"] == 2
    assert contents["lines"][0]["size"] == "14"
    assert [row["id"] for row in contents["members"]] == [member["id"]]
    # A member cannot hold its own lines; lines reject bad quantities.
    assert client.post(f"/api/tools-workspace/sets/{member['id']}/lines", headers=aa, json={"name": "X", "qty": 1}).status_code == 409
    assert client.post(f"/api/tools-workspace/sets/{head['id']}/lines", headers=aa, json={"name": "", "qty": 1}).status_code == 422
    assert client.post(f"/api/tools-workspace/sets/{head['id']}/lines", headers=aa, json={"name": "X", "qty": 0}).status_code == 422
    # Edit + remove round-trip.
    line_id = contents["lines"][0]["id"]
    edited = client.patch(f"/api/tools-workspace/sets/lines/{line_id}", headers=aa, json={"qty": 3})
    assert edited.status_code == 200
    assert edited.json()["qty"] == 3
    removed = client.delete(f"/api/tools-workspace/sets/lines/{line_id}", headers=aa)
    assert removed.status_code == 200
    assert removed.json()["lines"] == []


def test_open_requires_an_issued_set_and_is_idempotent():
    admin, token = admin_and_issuer()
    aa = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(aa, "E-1")
    head = make_tool(aa, "SET-1", "Gedore set")
    first = make_tool(aa, "T-1", "Spanner")
    make_eligible(aa, holder, head)
    make_eligible(aa, holder, first)
    make_set(aa, head, [first], [("Spanner", "14", 2)])
    # Not issued yet: no check.
    assert client.post("/api/tools-workspace/toolbox-checks", headers=aa, json={"set_tool_id": head["id"], "period": "2026-10"}).status_code == 409
    # A member can never have its own check.
    assert client.post("/api/tools-workspace/toolbox-checks", headers=aa, json={"set_tool_id": first["id"], "period": "2026-10"}).status_code == 409
    issue_set(auth, head, holder)
    opened = client.post("/api/tools-workspace/toolbox-checks", headers=aa, json={"set_tool_id": head["id"], "period": "2026-10"})
    assert opened.status_code == 201
    body = opened.json()
    assert body["check"]["holder_name"] == holder["name"]
    assert body["check"]["status"] == "open"
    assert [row["id"] for row in body["members"]] == [first["id"]]
    assert [line["name"] for line in body["lines"]] == ["Spanner"]
    reopened = client.post("/api/tools-workspace/toolbox-checks", headers=aa, json={"set_tool_id": head["id"], "period": "2026-10"})
    assert reopened.status_code == 201
    assert reopened.json()["check"]["id"] == body["check"]["id"]


def test_clean_submit_marks_no_incidents():
    admin, token = admin_and_issuer()
    aa = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(aa, "E-1")
    head = make_tool(aa, "SET-1", "Gedore set")
    first = make_tool(aa, "T-1", "Spanner")
    for tool in (head, first):
        make_eligible(aa, holder, tool)
    make_set(aa, head, [first], [("Spanner", "14", 2)])
    issue_set(auth, head, holder)
    check_id = client.post("/api/tools-workspace/toolbox-checks", headers=aa, json={"set_tool_id": head["id"], "period": "2026-10"}).json()["check"]["id"]
    lines = client.get(f"/api/tools-workspace/toolbox-checks/{check_id}", headers=aa).json()
    assert lines["items"] == []
    submitted = client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": [
        {"kind": "member", "ref_id": head["id"], "state": "available", "note": ""},
        {"kind": "member", "ref_id": first["id"], "state": "available", "note": ""},
        {"kind": "line", "ref_id": lines["check"]["id"] and client.get(f"/api/tools-workspace/sets/{head['id']}/contents", headers=aa).json()["lines"][0]["id"], "state": "available", "qty_found": 2, "note": ""},
    ]})
    assert submitted.status_code == 200
    assert submitted.json()["check"]["status"] == "submitted"
    incidents = client.get("/api/tools-workspace/compliance", headers=aa).json()["incidents"]
    assert incidents == []
    resubmit = client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": [
        {"kind": "member", "ref_id": head["id"], "state": "available", "note": ""},
        {"kind": "member", "ref_id": first["id"], "state": "available", "note": ""},
        {"kind": "line", "ref_id": client.get(f"/api/tools-workspace/sets/{head['id']}/contents", headers=aa).json()["lines"][0]["id"], "state": "available", "qty_found": 2, "note": ""},
    ]})
    assert resubmit.status_code == 409


def test_stolen_member_raises_one_deduped_incident():
    admin, token = admin_and_issuer()
    aa = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(aa, "E-1")
    head = make_tool(aa, "SET-1", "Gedore set")
    first = make_tool(aa, "T-1", "Spanner")
    for tool in (head, first):
        make_eligible(aa, holder, tool)
    make_set(aa, head, [first])
    issue_set(auth, head, holder)
    check_id = client.post("/api/tools-workspace/toolbox-checks", headers=aa, json={"set_tool_id": head["id"], "period": "2026-10"}).json()["check"]["id"]
    payload = {"items": [
        {"kind": "member", "ref_id": head["id"], "state": "available", "note": ""},
        {"kind": "member", "ref_id": first["id"], "state": "stolen", "qty_found": 0, "note": "Gone from the box", "how_happened": "Taken overnight from the crib", "prevention": "Lock the crib and sign the register"},
    ]}
    assert client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json=payload).status_code == 200
    incidents = client.get("/api/tools-workspace/compliance", headers=aa).json()["incidents"]
    assert len(incidents) == 1
    assert incidents[0]["incident_type"] == "stolen"
    assert incidents[0]["employee_id"] == holder["id"]
    # Next month, the same unresolved theft reports no duplicate incident.
    client.post(f"/api/tools-workspace/tools/{head['id']}/return", headers=auth, json={"location": "Workshop", "condition": "Good"})
    issue_set(auth, head, holder)
    november = client.post("/api/tools-workspace/toolbox-checks", headers=aa, json={"set_tool_id": head["id"], "period": "2026-11"}).json()["check"]["id"]
    assert client.post(f"/api/tools-workspace/toolbox-checks/{november}/submit", headers=aa, json=payload).status_code == 200
    incidents = client.get("/api/tools-workspace/compliance", headers=aa).json()["incidents"]
    assert len(incidents) == 1


def test_submit_validation_names_the_gap():
    admin, token = admin_and_issuer()
    aa = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(aa, "E-1")
    head = make_tool(aa, "SET-1", "Gedore set")
    first = make_tool(aa, "T-1", "Spanner")
    for tool in (head, first):
        make_eligible(aa, holder, tool)
    make_set(aa, head, [first], [("Spanner", "14", 2)])
    issue_set(auth, head, holder)
    line_id = client.get(f"/api/tools-workspace/sets/{head['id']}/contents", headers=aa).json()["lines"][0]["id"]
    check_id = client.post("/api/tools-workspace/toolbox-checks", headers=aa, json={"set_tool_id": head["id"], "period": "2026-10"}).json()["check"]["id"]
    good = [
        {"kind": "member", "ref_id": head["id"], "state": "available", "note": ""},
        {"kind": "member", "ref_id": first["id"], "state": "available", "note": ""},
        {"kind": "line", "ref_id": line_id, "state": "available", "qty_found": 2, "note": ""},
    ]
    # Incomplete coverage names the missing piece.
    short = client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": good[:2]})
    assert short.status_code == 422
    assert "Spanner" in short.json()["detail"]
    # Duplicates, unknown refs and bad counts are refused.
    assert client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": good[:1] + good[:1] + good[1:]}).status_code == 422
    assert client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": good[:2] + [{"kind": "line", "ref_id": "nope", "state": "available", "note": ""}]}).status_code == 422
    over = [dict(good[2], qty_found=5)]
    assert client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": good[:2] + over}).status_code == 422
    # Non-available states need reasons; damage and theft need narratives.
    silent = [dict(good[1], state="missing", qty_found=0, note="")]
    assert client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": [good[0], silent, good[2]]}).status_code == 422
    vague = [dict(good[1], state="damaged", qty_found=1, note="Broken")]
    assert client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": [good[0], vague, good[2]]}).status_code == 422
    theft = [dict(good[1], state="stolen", qty_found=0, note="Gone", how_happened="Taken")]
    assert client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": [good[0], theft, good[2]]}).status_code == 422
    assert client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": good}).status_code == 200


def test_due_and_overdue_flags_and_filters():
    admin, token = admin_and_issuer()
    aa = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(aa, "E-1")
    head = make_tool(aa, "SET-1", "Gedore set")
    first = make_tool(aa, "T-1", "Spanner")
    other = make_tool(aa, "T-2", "Hammer")
    for tool in (head, first):
        make_eligible(aa, holder, tool)
    make_set(aa, head, [first])
    issue_set(auth, head, holder)
    alerts = {alert["key"] for alert in client.get("/api/tools-workspace/notifications", headers=auth).json()["alerts"]}
    current = next(key.split(":")[2] for key in alerts if key.startswith(f"toolbox:{head['id']}:"))
    assert f"toolbox:{head['id']}:{current}" in alerts
    # Submitting the current month keeps an older missing month flagged overdue.
    check_id = client.post("/api/tools-workspace/toolbox-checks", headers=aa, json={"set_tool_id": head["id"]}).json()["check"]["id"]
    client.post(f"/api/tools-workspace/toolbox-checks/{check_id}/submit", headers=aa, json={"items": [
        {"kind": "member", "ref_id": head["id"], "state": "available", "note": ""},
        {"kind": "member", "ref_id": first["id"], "state": "borrowed", "qty_found": 0, "note": "Lent to night shift", "follow_up": "Back Friday"},
    ]})
    listed = client.get("/api/tools-workspace/toolbox-checks", headers=aa).json()
    assert listed[0]["exceptions"]["borrowed"] == 1
    assert listed[0]["holder_name"] == holder["name"]
    borrowed_only = client.get("/api/tools-workspace/toolbox-checks?state=borrowed", headers=aa).json()
    assert [row["id"] for row in borrowed_only] == [check_id]
    assert client.get("/api/tools-workspace/toolbox-checks?state=stolen", headers=aa).json() == []
    mine = client.get(f"/api/tools-workspace/toolbox-checks?employee_id={holder['id']}", headers=aa).json()
    assert [row["id"] for row in mine] == [check_id]
    # Other tools never appear in the checklist world.
    assert other["id"] not in {row["set_tool_id"] for row in listed}
