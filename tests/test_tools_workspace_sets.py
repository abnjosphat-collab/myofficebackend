"""Tool sets: a set head fans a loan out to its members as one atomic movement."""
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


def register(username: str, can_issue: bool = False):
    response = client.post("/api/tools-workspace/auth/register", json={"name": username.title(), "username": username, "password": "secret12", "can_issue": can_issue})
    assert response.status_code == 201
    return response.json()["token"]


def admin_and_issuer(department: str = "Engineering"):
    admin = register("admin")
    viewer = register(f"{department.lower().replace(' ', '-')}-issuer")
    accounts = client.get("/api/tools-workspace/accounts", headers={"Authorization": f"Bearer {admin}"}).json()
    target = next(account for account in accounts if account["role"] == "viewer")
    promoted = client.patch(f"/api/tools-workspace/accounts/{target['id']}", headers={"Authorization": f"Bearer {admin}"}, json={"role": "issuer", "department": department})
    assert promoted.status_code == 200
    return admin, viewer


def make_employee(admin_auth, number, name="Alex"):
    employee = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": number, "name": name, "department": "Engineering"}).json()
    assert "id" in employee
    return employee


def make_tool(admin_auth, register_number, name="Spanner"):
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": register_number, "name": name, "storage_location": "Workshop"}).json()
    assert "id" in tool
    return tool


def make_eligible_and_current(admin_auth, employee, tool):
    competency = client.post("/api/tools-workspace/competencies", headers=admin_auth, json={"employee_id": employee["id"], "tool_id": tool["id"], "trained": True, "qualified": True, "authorized": True})
    assert competency.status_code == 201
    for inspection_type in ("monthly", "quarterly"):
        inspection = client.post(f"/api/tools-workspace/tools/{tool['id']}/inspections", headers=admin_auth, json={"inspection_type": inspection_type, "outcome": "passed", "next_due_at": "2035-01-01T00:00:00Z"})
        assert inspection.status_code == 201


def make_set(admin_auth, head, members):
    # Member linking through the API is retired (POST answers 410); legacy
    # links are rows in the table, so tests build them directly.
    for member in members:
        tools_workspace._insert("set_members", {"id": str(uuid.uuid4()), "set_tool_id": head["id"], "member_tool_id": member["id"], "qty": 1, "created_by": "tests", "created_at": tools_workspace._now()})
    return client.get(f"/api/tools-workspace/tools/{head['id']}/members", headers=admin_auth).json()


def test_set_forms_lists_members_and_intersects_eligibility():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(admin_auth, "E-1")
    head = make_tool(admin_auth, "SET-1", "Gedore 228-piece set")
    first = make_tool(admin_auth, "T-1", "Spanner 13mm")
    second = make_tool(admin_auth, "T-2", "Spanner 17mm")
    make_eligible_and_current(admin_auth, holder, head)
    make_eligible_and_current(admin_auth, holder, first)
    # Holder is NOT approved for the second member, so the set must hide them.
    make_set(admin_auth, head, [first, second])
    tools = {tool["register_number"]: tool for tool in client.get("/api/tools-workspace/tools", headers=auth).json()}
    assert sorted(tools["SET-1"]["member_ids"]) == sorted([first["id"], second["id"]])
    assert tools["T-1"]["set_head_id"] == head["id"]
    assert tools["T-1"]["set_head_name"] == "Gedore 228-piece set"
    assert "member_ids" not in tools["T-1"]
    eligible = [entry["employee_number"] for entry in tools["SET-1"]["eligible_employees"]]
    assert eligible == []
    make_eligible_and_current(admin_auth, holder, second)
    tools = {tool["register_number"]: tool for tool in client.get("/api/tools-workspace/tools", headers=auth).json()}
    assert [entry["employee_number"] for entry in tools["SET-1"]["eligible_employees"]] == ["E-1"]


def test_member_cannot_move_alone_and_loan_fans_out_and_back():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(admin_auth, "E-1")
    head = make_tool(admin_auth, "SET-1", "Gedore set")
    first = make_tool(admin_auth, "T-1", "Spanner")
    second = make_tool(admin_auth, "T-2", "Ratchet")
    for tool in (head, first, second):
        make_eligible_and_current(admin_auth, holder, tool)
    make_set(admin_auth, head, [first, second])
    lone = client.post(f"/api/tools-workspace/tools/{first['id']}/issue", headers=auth, json={"employee_id": holder["id"], "location": "Pit"})
    assert lone.status_code == 409
    assert "SET-1" in lone.json()["detail"]
    issued = client.post(f"/api/tools-workspace/tools/{head['id']}/issue", headers=auth, json={"employee_id": holder["id"], "location": "Pit", "expected_return_at": "2030-01-02T10:00:00Z"})
    assert issued.status_code == 200
    assert issued.json()["tool"]["status"] == "issued"
    assert "group_id" in issued.json()
    tools = {tool["register_number"]: tool for tool in client.get("/api/tools-workspace/tools", headers=auth).json()}
    for register in ("SET-1", "T-1", "T-2"):
        custody = tools[register]["custody"]
        assert custody["employee_id"] == holder["id"]
        assert custody["expected_return_at"] == "2030-01-02T10:00:00Z"
    returned = client.post(f"/api/tools-workspace/tools/{head['id']}/return", headers=auth, json={"location": "Workshop", "condition": "Good", "member_conditions": {second["id"]: "Damaged"}})
    assert returned.status_code == 200
    tools = {tool["register_number"]: tool for tool in client.get("/api/tools-workspace/tools", headers=auth).json()}
    assert tools["T-1"]["status"] == "available"
    assert tools["T-2"]["status"] == "attention"
    assert tools["T-2"]["condition"] == "Damaged"
    assert tools["SET-1"]["status"] == "attention"
    assert tools["SET-1"]["condition"] == "Damaged"


def test_partial_ineligibility_blocks_the_whole_set():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(admin_auth, "E-1")
    head = make_tool(admin_auth, "SET-1", "Gedore set")
    first = make_tool(admin_auth, "T-1", "Spanner")
    second = make_tool(admin_auth, "T-2", "Ratchet")
    make_eligible_and_current(admin_auth, holder, head)
    make_eligible_and_current(admin_auth, holder, first)
    make_set(admin_auth, head, [first, second])
    refused = client.post(f"/api/tools-workspace/tools/{head['id']}/issue", headers=auth, json={"employee_id": holder["id"], "location": "Pit"})
    assert refused.status_code == 409
    assert "T-2" in refused.json()["detail"]


def test_member_link_endpoint_is_retired_but_reads_and_removals_stay():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    head = make_tool(admin_auth, "SET-1", "Gedore set")
    first = make_tool(admin_auth, "T-1", "Spanner")
    # New links are refused with a plain sentence, even for valid tools.
    refused = client.post(f"/api/tools-workspace/tools/{head['id']}/members", headers=admin_auth, json={"member_ids": [first["id"]]})
    assert refused.status_code == 410
    assert "set type" in refused.json()["detail"]
    assert client.post(f"/api/tools-workspace/tools/missing/members", headers=admin_auth, json={"member_ids": [first["id"]]}).status_code == 404
    # Reading and removing legacy links still works for old browser tabs.
    make_set(admin_auth, head, [first])
    assert client.get(f"/api/tools-workspace/tools/{head['id']}/members", headers=admin_auth).json()["members"][0]["id"] == first["id"]
    removed = client.delete(f"/api/tools-workspace/tools/{head['id']}/members/{first['id']}", headers=admin_auth)
    assert removed.status_code == 200
    assert removed.json()["members"] == []


def test_legacy_links_still_guard_loans_archives_and_gate_passes():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(admin_auth, "E-1")
    head = make_tool(admin_auth, "SET-1", "Gedore set")
    first = make_tool(admin_auth, "T-1", "Spanner")
    for tool in (head, first):
        competency = client.post("/api/tools-workspace/competencies", headers=admin_auth, json={"employee_id": holder["id"], "tool_id": tool["id"], "trained": True, "qualified": True, "authorized": True})
        assert competency.status_code == 201
    make_set(admin_auth, head, [first])
    # A legacy member still cannot move alone, and a lone member cannot ride a gate pass.
    assert client.post(f"/api/tools-workspace/tools/{first['id']}/issue", headers=auth, json={"employee_id": holder["id"], "location": "Pit"}).status_code == 409
    refused_pass = client.post("/api/tools-workspace/gate-passes", headers=admin_auth, json={"movement_scope": "internal", "department": "Engineering", "destination": "Shaft", "purpose": "Day shift", "expected_out_at": "2030-01-02T08:00:00Z", "tool_ids": [first["id"]]})
    assert refused_pass.status_code == 409
    assert "whole set" in refused_pass.json()["detail"]
    # Archiving is blocked while the legacy link exists.
    assert client.post(f"/api/tools-workspace/tools/{head['id']}/archive", headers=admin_auth).status_code == 409
    assert client.post(f"/api/tools-workspace/tools/{first['id']}/archive", headers=admin_auth).status_code == 409


def test_set_issue_undo_redo_restores_every_member():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(admin_auth, "E-1")
    head = make_tool(admin_auth, "SET-1", "Gedore set")
    first = make_tool(admin_auth, "T-1", "Spanner")
    for tool in (head, first):
        make_eligible_and_current(admin_auth, holder, tool)
    make_set(admin_auth, head, [first])
    assert client.post(f"/api/tools-workspace/tools/{head['id']}/issue", headers=auth, json={"employee_id": holder["id"], "location": "Pit"}).status_code == 200
    undone = client.post("/api/tools-workspace/changes/undo", headers=auth)
    assert undone.status_code == 200
    assert undone.json()["group_id"]
    tools = {tool["register_number"]: tool for tool in client.get("/api/tools-workspace/tools", headers=auth).json()}
    assert tools["SET-1"]["custody"] is None
    assert tools["T-1"]["custody"] is None
    redone = client.post("/api/tools-workspace/changes/redo", headers=auth)
    assert redone.status_code == 200
    tools = {tool["register_number"]: tool for tool in client.get("/api/tools-workspace/tools", headers=auth).json()}
    assert tools["SET-1"]["custody"]["employee_id"] == holder["id"]
    assert tools["T-1"]["custody"]["employee_id"] == holder["id"]


def test_head_mark_ready_needs_every_member_fit():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    holder = make_employee(admin_auth, "E-1")
    head = make_tool(admin_auth, "SET-1", "Gedore set")
    first = make_tool(admin_auth, "T-1", "Spanner")
    for tool in (head, first):
        make_eligible_and_current(admin_auth, holder, tool)
    make_set(admin_auth, head, [first])
    client.post(f"/api/tools-workspace/tools/{head['id']}/issue", headers=auth, json={"employee_id": holder["id"], "location": "Pit"})
    client.post(f"/api/tools-workspace/tools/{head['id']}/return", headers=auth, json={"location": "Workshop", "condition": "Good", "member_conditions": {first["id"]: "Damaged"}})
    refused = client.post(f"/api/tools-workspace/tools/{head['id']}/mark-ready", headers=admin_auth, json={"resolution_note": "Set repaired"})
    assert refused.status_code == 409
    assert "T-1" in refused.json()["detail"]
    assert client.post(f"/api/tools-workspace/tools/{first['id']}/mark-ready", headers=admin_auth, json={"resolution_note": "Replaced jaw"}).status_code == 200
    assert client.post(f"/api/tools-workspace/tools/{head['id']}/mark-ready", headers=admin_auth, json={"resolution_note": "Set checked over"}).status_code == 200
