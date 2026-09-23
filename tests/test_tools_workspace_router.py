from fastapi.testclient import TestClient

from app.routers import tools_workspace
from main import app


client = TestClient(app)


def setup_function():
    tools_workspace._reset_for_tests()


def register(username: str, can_issue: bool):
    response = client.post("/api/tools-workspace/auth/register", json={"name": username.title(), "username": username, "password": "secret12", "can_issue": can_issue})
    assert response.status_code == 201
    return response.json()["token"]


def test_registration_accepts_email_style_username():
    response = client.post("/api/tools-workspace/auth/register", json={"name": "Jos Phat", "username": "josphat@gmail.com", "password": "secret12", "can_issue": True})
    assert response.status_code == 201
    assert response.json()["account"]["username"] == "josphat@gmail.com"


def test_viewer_cannot_issue_but_issuer_creates_complete_history():
    issuer = register("issuer", True)
    viewer = register("viewer", False)
    auth = {"Authorization": f"Bearer {issuer}"}
    employee = client.post("/api/tools-workspace/employees", headers=auth, json={"employee_number": "E-1", "name": "Alex", "department": "Engineering"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=auth, json={"register_number": "T-1", "name": "Clamp meter", "storage_location": "Workshop"}).json()
    payload = {"employee_id": employee["id"], "location": "Plant 4", "job_reference": "WO-9"}
    denied = client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers={"Authorization": f"Bearer {viewer}"}, json=payload)
    assert denied.status_code == 403
    issued = client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers=auth, json=payload)
    assert issued.status_code == 200
    returned = client.post(f"/api/tools-workspace/tools/{tool['id']}/return", headers=auth, json={"location": "Tool room", "condition": "Good"})
    assert returned.status_code == 200
    trail = client.get("/api/tools-workspace/history", headers=auth).json()
    assert [event["action"] for event in trail[:2]] == ["return", "issue"]
    assert all(event["actor_name"] == "Issuer" for event in trail[:2])
    assert all(event["event_at"] for event in trail[:2])


def test_transfer_extension_edit_archive_and_undo_are_durable():
    token = register("operator", True)
    auth = {"Authorization": f"Bearer {token}"}
    first = client.post("/api/tools-workspace/employees", headers=auth, json={"employee_number": "E-1", "name": "Alex", "department": "Engineering"}).json()
    second = client.post("/api/tools-workspace/employees", headers=auth, json={"employee_number": "E-2", "name": "Jordan", "department": "Mining"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=auth, json={"register_number": "T-9", "name": "Drill", "storage_location": "Workshop"}).json()
    assert client.patch(f"/api/tools-workspace/tools/{tool['id']}", headers=auth, json={"name": "Cordless drill"}).status_code == 200
    assert client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=auth, json={"kind": "issue", "employee_id": first["id"], "location": "Plant", "expected_return_at": "2030-01-02T10:00:00Z"}).status_code == 200
    assert client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=auth, json={"kind": "transfer", "employee_id": second["id"], "location": "Pit"}).status_code == 200
    assert client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=auth, json={"kind": "extend", "expected_return_at": "2030-01-03T10:00:00Z"}).status_code == 200
    returned = client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=auth, json={"kind": "return", "location": "Store", "condition": "Good"})
    assert returned.status_code == 200
    archived = client.post(f"/api/tools-workspace/tools/{tool['id']}/archive", headers=auth)
    assert archived.status_code == 200 and archived.json()["archived"] is True
    undone = client.post("/api/tools-workspace/changes/undo", headers=auth)
    assert undone.status_code == 200 and undone.json()["tool"]["archived"] is False
    redone = client.post("/api/tools-workspace/changes/redo", headers=auth)
    assert redone.status_code == 200 and redone.json()["tool"]["archived"] is True


def test_registers_start_empty():
    token = register("first", True)
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/tools-workspace/tools", headers=auth).json() == []
    assert client.get("/api/tools-workspace/employees", headers=auth).json() == []
    assert client.get("/api/tools-workspace/history", headers=auth).json() == []


def test_usage_errors_and_feedback_are_available_to_analytics():
    token = register("observer", True)
    auth = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/tools-workspace/analytics/usage", headers=auth, json={"event": "opened equipment register"}).status_code == 202
    assert client.post("/api/tools-workspace/analytics/errors", headers=auth, json={"message": "Example failure", "source": "prototype"}).status_code == 202
    assert client.post("/api/tools-workspace/feedback", headers=auth, data={"text": "Make search faster"}).status_code == 201
    data = client.get("/api/tools-workspace/analytics", headers=auth).json()
    assert data["totals"] == {"usage": 1, "errors": 1, "feedback": 1}


def test_import_and_attachment_metadata_persist():
    token = register("storekeeper", True)
    auth = {"Authorization": f"Bearer {token}"}
    imported = client.post("/api/tools-workspace/imports/commit", headers=auth, json={"target": "equipment", "rows": [{"register_number": "I-1", "name": "Meter", "storage_location": "Store"}]})
    assert imported.status_code == 200 and imported.json()["accepted_count"] == 1
    tool = imported.json()["accepted"][0]
    attached = client.post(f"/api/tools-workspace/tools/{tool['id']}/evidence", headers=auth, files=[("files", ("condition.jpg", b"image-bytes", "image/jpeg"))])
    assert attached.status_code == 201
    reloaded = client.get("/api/tools-workspace/tools", headers=auth).json()
    assert reloaded[0]["evidence"][0]["original_name"] == "condition.jpg"
