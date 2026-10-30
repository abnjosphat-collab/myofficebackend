"""Set types: enter contents once, make numbered sets, issue to several people."""
import pytest
from fastapi.testclient import TestClient

from app.rate_limit import limiter
from app.routers import tools_workspace
from app.routers import tools_workspace_set_types as set_types
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


def make_type(admin_auth, title="Gedore socket set", lines=3):
    body = {"title": title, "make_model": "Gedore", "category": "Hand tools",
            "lines": [{"name": f"Spanner {size}mm", "size": str(size), "qty": 1} for size in range(10, 10 + lines)]}
    response = client.post("/api/tools-workspace/set-types", headers=admin_auth, json=body)
    assert response.status_code == 201
    return response.json()


def issue(admin_auth, auth, type_id, employee_ids, **extra):
    body = {"employee_ids": employee_ids, "location": "Pit", **extra}
    return client.post(f"/api/tools-workspace/set-types/{type_id}/issue", headers=auth, json=body)


def test_create_type_numbers_lines_from_one():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    created = make_type(admin_auth, lines=200)
    assert created["set_type"]["title"] == "Gedore socket set"
    assert [line["line_no"] for line in created["lines"]] == list(range(1, 201))
    listed = client.get("/api/tools-workspace/set-types", headers=admin_auth).json()
    assert listed[0]["line_count"] == 200
    assert listed[0]["physical_total"] == 0


def test_issue_to_six_makes_numbered_sets_with_contiguous_pieces():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    created = make_type(admin_auth, lines=200)
    people = [make_employee(admin_auth, f"E-{number}", f"Person {number}") for number in range(1, 7)]
    response = issue(admin_auth, auth, created["set_type"]["id"], [person["id"] for person in people])
    assert response.status_code == 201
    payload = response.json()
    assert payload["issued"] == 6 and payload["failed"] == 0
    numbers = sorted(row["register_number"] for row in payload["results"])
    assert numbers == ["SET-01", "SET-02", "SET-03", "SET-04", "SET-05", "SET-06"]
    for row in payload["results"]:
        assert row["piece_count"] == 200
        pieces = [item for item in tools_workspace._all("set_lines") if item["set_tool_id"] == row["set_id"]]
        assert [item["line_no"] for item in sorted(pieces, key=lambda item: item["line_no"])] == list(range(1, 201))
        assert [item["piece_number"] for item in sorted(pieces, key=lambda item: item["line_no"])] == [f"{row['register_number']}/{sequence:03d}" for sequence in range(1, 201)]
    tools = {tool["register_number"]: tool for tool in client.get("/api/tools-workspace/tools", headers=auth).json() if tool.get("set_type_id")}
    assert set(tools) == set(numbers)
    assert all(tool["custody"] for tool in tools.values())


def test_free_set_in_store_is_reused():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    created = make_type(admin_auth, lines=2)
    type_id = created["set_type"]["id"]
    store = client.post(f"/api/tools-workspace/set-types/{type_id}/make-in-store", headers=admin_auth, json={"count": 2, "department": "Engineering"}).json()["sets"]
    person = make_employee(admin_auth, "E-1")
    first = issue(admin_auth, auth, type_id, [person["id"]]).json()["results"][0]
    assert first["register_number"] == store[0]["register_number"]
    assert len([row for row in tools_workspace._all("tools") if row.get("set_type_id") == type_id]) == 2


def test_inactive_person_is_reported_not_blanket():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    created = make_type(admin_auth, lines=2)
    people = [make_employee(admin_auth, f"E-{number}", f"Person {number}") for number in range(1, 6)]
    quiet = make_employee(admin_auth, "E-9", "Peter Ncube")
    client.patch(f"/api/tools-workspace/employees/{quiet['id']}", headers=admin_auth, json={"active": False})
    payload = issue(admin_auth, auth, created["set_type"]["id"], [person["id"] for person in people] + [quiet["id"]]).json()
    assert payload["issued"] == 5 and payload["failed"] == 1
    failed = next(row for row in payload["results"] if not row["ok"])
    assert failed["employee_name"] == "Peter Ncube"
    assert failed["reason"] == "inactive"


def test_issue_accepts_mine_numbers_with_conditions_keyed_by_them():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    created = make_type(admin_auth, lines=2)
    person = make_employee(admin_auth, "E-1")
    payload = issue(admin_auth, auth, created["set_type"]["id"], [person["employee_number"]], conditions={person["employee_number"]: {"1": "Damaged"}}).json()
    assert payload["issued"] == 1 and payload["failed"] == 0
    issued = payload["results"][0]
    assert issued["employee_name"] == person["name"]
    pieces = sorted([item for item in tools_workspace._all("set_lines") if item["set_tool_id"] == issued["set_id"]], key=lambda item: item["line_no"])
    assert pieces[0]["condition"] == "Damaged"
    assert pieces[1]["condition"] == "Good"


def test_idempotent_retry_replays():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    created = make_type(admin_auth, lines=2)
    people = [make_employee(admin_auth, f"E-{number}") for number in range(1, 3)]
    first = issue(admin_auth, auth, created["set_type"]["id"], [person["id"] for person in people], idempotency_key="batch-1").json()
    assert first["replayed"] is False
    second = issue(admin_auth, auth, created["set_type"]["id"], [person["id"] for person in people], idempotency_key="batch-1").json()
    assert second["replayed"] is True
    assert [row["register_number"] for row in second["results"]] == [row["register_number"] for row in first["results"]]
    assert len([row for row in tools_workspace._all("tools") if row.get("set_type_id") == created["set_type"]["id"]]) == 2


def test_return_with_missing_piece_puts_set_in_attention():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    created = make_type(admin_auth, lines=3)
    person = make_employee(admin_auth, "E-1")
    issued = issue(admin_auth, auth, created["set_type"]["id"], [person["id"]]).json()["results"][0]
    pieces = sorted([item for item in tools_workspace._all("set_lines") if item["set_tool_id"] == issued["set_id"]], key=lambda item: item["line_no"])
    missing = pieces[1]
    returned = client.post(f"/api/tools-workspace/sets/{issued['set_id']}/return", headers=auth,
                           json={"location": "Workshop", "piece_status": {missing["id"]: "missing"},
                                   "piece_conditions": {missing["id"]: "Missing parts"}}).json()
    assert returned["tool"]["status"] == "attention"
    assert next(item for item in returned["pieces"] if item["id"] == missing["id"])["status"] == "missing"
    refused = client.post(f"/api/tools-workspace/tools/{issued['set_id']}/mark-ready", headers=admin_auth, json={"resolution_note": "Checked"})
    assert refused.status_code == 409
    assert missing["piece_number"] in refused.json()["detail"]
    fixed = client.patch(f"/api/tools-workspace/sets/pieces/{missing['id']}", headers=auth,
                         json={"status": "present", "condition": "Good"}).json()
    assert fixed["piece"]["status"] == "present"
    assert client.post(f"/api/tools-workspace/tools/{issued['set_id']}/mark-ready", headers=admin_auth, json={"resolution_note": "Piece found"}).status_code == 200


def test_type_edit_leaves_existing_pieces_alone():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    created = make_type(admin_auth, lines=2)
    type_id = created["set_type"]["id"]
    person = make_employee(admin_auth, "E-1")
    issued = issue(admin_auth, auth, type_id, [person["id"]]).json()["results"][0]
    before = sorted(item["name"] for item in tools_workspace._all("set_lines") if item["set_tool_id"] == issued["set_id"])
    line_id = created["lines"][0]["id"]
    client.patch(f"/api/tools-workspace/set-type-lines/{line_id}", headers=admin_auth, json={"name": "Renamed spanner"})
    client.post(f"/api/tools-workspace/set-types/{type_id}/lines", headers=admin_auth, json={"name": "Extra bit"})
    after = sorted(item["name"] for item in tools_workspace._all("set_lines") if item["set_tool_id"] == issued["set_id"])
    assert after == before
    assert len(client.get(f"/api/tools-workspace/set-types/{type_id}", headers=admin_auth).json()["lines"]) == 3


def test_old_member_link_endpoint_answers_410():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    head = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "H-1", "name": "Head", "storage_location": "Workshop"}).json()
    member = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "M-1", "name": "Member", "storage_location": "Workshop"}).json()
    refused = client.post(f"/api/tools-workspace/tools/{head['id']}/members", headers=admin_auth, json={"member_ids": [member["id"]]})
    assert refused.status_code == 410
    assert client.get(f"/api/tools-workspace/tools/{head['id']}/members", headers=admin_auth).status_code == 200


def test_toolbox_check_lists_pieces_and_missing_raises_head_incident():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    created = make_type(admin_auth, lines=3)
    type_id = created["set_type"]["id"]
    person = make_employee(admin_auth, "E-1")
    issued = issue(admin_auth, auth, type_id, [person["id"]]).json()["results"][0]
    opened = client.post("/api/tools-workspace/toolbox-checks", headers=auth, json={"set_tool_id": issued["set_id"], "period": "2026-10"}).json()
    assert len(opened["lines"]) == 3
    items = [{"kind": "line", "ref_id": line["id"], "state": "missing" if index == 0 else "available", "note": "Gone" if index == 0 else ""} for index, line in enumerate(opened["lines"])]
    submitted = client.post(f"/api/tools-workspace/toolbox-checks/{opened['check']['id']}/submit", headers=auth, json={"items": items}).json()
    assert submitted["check"]["status"] == "submitted"
    missing_piece = next(item for item in submitted["items"] if item["state"] == "missing")
    incidents = [row for row in tools_workspace._all("incidents") if row.get("tool_id") == issued["set_id"]]
    assert len(incidents) == 1
    assert missing_piece["name_snapshot"].split(" ")[0] in incidents[0]["explanation"]
    piece = tools_workspace._find("set_lines", "id", missing_piece["line_id"])
    assert piece is not None
    assert piece["status"] == "missing"


def test_undo_of_set_issue_restores_every_row():
    admin, token = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    auth = {"Authorization": f"Bearer {token}"}
    created = make_type(admin_auth, lines=2)
    person = make_employee(admin_auth, "E-1")
    issued = issue(admin_auth, auth, created["set_type"]["id"], [person["id"]]).json()["results"][0]
    undone = client.post("/api/tools-workspace/changes/undo", headers=auth)
    assert undone.status_code == 200
    tools = {tool["register_number"]: tool for tool in client.get("/api/tools-workspace/tools", headers=auth).json()}
    assert tools[issued["register_number"]]["custody"] is None
    redone = client.post("/api/tools-workspace/changes/redo", headers=auth)
    assert redone.status_code == 200
    tools = {tool["register_number"]: tool for tool in client.get("/api/tools-workspace/tools", headers=auth).json()}
    assert tools[issued["register_number"]]["custody"]["employee_id"] == person["id"]
