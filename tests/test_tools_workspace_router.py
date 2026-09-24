from fastapi.testclient import TestClient

from app.routers import tools_workspace
from main import app


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


def test_registration_accepts_email_style_username():
    response = client.post("/api/tools-workspace/auth/register", json={"name": "Jos Phat", "username": "josphat@gmail.com", "password": "secret12", "can_issue": True})
    assert response.status_code == 201
    assert response.json()["account"]["username"] == "josphat@gmail.com"
    assert response.json()["account"]["role"] == "admin"
    assert response.json()["account"]["can_issue"] is False


def test_viewer_cannot_issue_but_issuer_creates_complete_history():
    admin, issuer = admin_and_issuer()
    viewer = register("viewer", False)
    auth = {"Authorization": f"Bearer {issuer}"}
    admin_auth = {"Authorization": f"Bearer {admin}"}
    employee = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-1", "name": "Alex", "department": "Engineering"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-1", "name": "Clamp meter", "storage_location": "Workshop"}).json()
    payload = {"employee_id": employee["id"], "location": "Plant 4", "job_reference": "WO-9"}
    denied = client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers={"Authorization": f"Bearer {viewer}"}, json=payload)
    assert denied.status_code == 403
    issued = client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers=auth, json=payload)
    assert issued.status_code == 200
    returned = client.post(f"/api/tools-workspace/tools/{tool['id']}/return", headers=auth, json={"location": "Tool room", "condition": "Good"})
    assert returned.status_code == 200
    trail = client.get("/api/tools-workspace/history", headers=auth).json()
    assert [event["action"] for event in trail[:2]] == ["return", "issue"]
    assert all(event["actor_name"] == "Engineering-Issuer" for event in trail[:2])
    assert all(event["event_at"] for event in trail[:2])


def test_admin_assigns_departmental_issuer_but_cannot_issue():
    admin, issuer = admin_and_issuer("Engineering")
    admin_auth = {"Authorization": f"Bearer {admin}"}
    issuer_auth = {"Authorization": f"Bearer {issuer}"}
    employee = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-10", "name": "Nyasha", "department": "Engineering"}).json()
    engineering_tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "ENG-10", "name": "Drill", "storage_location": "Workshop", "department": "Engineering"}).json()
    mining_tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "MIN-10", "name": "Lamp", "storage_location": "Store", "department": "Mining"}).json()

    denied_admin = client.post(f"/api/tools-workspace/tools/{engineering_tool['id']}/commands", headers=admin_auth, json={"kind": "issue", "employee_id": employee["id"], "location": "Plant"})
    denied_department = client.post(f"/api/tools-workspace/tools/{mining_tool['id']}/commands", headers=issuer_auth, json={"kind": "issue", "employee_id": employee["id"], "location": "Pit"})
    allowed = client.post(f"/api/tools-workspace/tools/{engineering_tool['id']}/commands", headers=issuer_auth, json={"kind": "issue", "employee_id": employee["id"], "location": "Plant", "assigned_equipment": ["Conveyor CV-01", "Pump P-12"]})

    assert denied_admin.status_code == 403
    assert denied_department.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["tool"]["custody"]["assigned_equipment"] == ["Conveyor CV-01", "Pump P-12"]


def test_repaired_equipment_can_be_marked_ready_with_an_audit_event():
    admin, issuer = admin_and_issuer("Engineering")
    viewer = register("viewer")
    admin_auth = {"Authorization": f"Bearer {admin}"}
    issuer_auth = {"Authorization": f"Bearer {issuer}"}
    viewer_auth = {"Authorization": f"Bearer {viewer}"}
    employee = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-READY", "name": "Tafadzwa", "department": "Engineering"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-READY", "name": "Impact drill", "storage_location": "Workshop", "department": "Engineering"}).json()
    client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers=issuer_auth, json={"employee_id": employee["id"], "location": "Crusher"})
    held = client.post(f"/api/tools-workspace/tools/{tool['id']}/return", headers=issuer_auth, json={"location": "Workshop", "condition": "Damaged", "notes": "Trigger switch failed"})
    assert held.status_code == 200 and held.json()["tool"]["status"] == "attention"

    denied = client.post(f"/api/tools-workspace/tools/{tool['id']}/mark-ready", headers=viewer_auth, json={"resolution_note": "Switch replaced and function tested"})
    released = client.post(f"/api/tools-workspace/tools/{tool['id']}/mark-ready", headers=issuer_auth, json={"resolution_note": "Switch replaced and function tested"})

    assert denied.status_code == 403
    assert released.status_code == 200
    assert released.json()["status"] == "available"
    assert released.json()["condition"] == "Good"
    assert released.json()["notes"] == "Switch replaced and function tested"
    trail = client.get("/api/tools-workspace/history", headers=viewer_auth).json()
    assert trail[0]["action"] == "released"
    assert "Switch replaced" in trail[0]["detail"]


def test_viewer_cannot_create_register_records():
    admin = register("admin")
    viewer = register("viewer", False)
    viewer_auth = {"Authorization": f"Bearer {viewer}"}

    employee = client.post("/api/tools-workspace/employees", headers=viewer_auth, json={"employee_number": "E-2", "name": "Viewer Person", "department": "Engineering"})
    tool = client.post("/api/tools-workspace/tools", headers=viewer_auth, json={"register_number": "T-2", "name": "Viewer Tool", "storage_location": "Workshop"})

    assert employee.status_code == 403
    assert tool.status_code == 403
    admin_auth = {"Authorization": f"Bearer {admin}"}
    assert client.get("/api/tools-workspace/employees", headers=admin_auth).json() == []
    assert client.get("/api/tools-workspace/tools", headers=admin_auth).json() == []


def test_equipment_specifications_are_editable_and_audited():
    admin = register("admin")
    auth = {"Authorization": f"Bearer {admin}"}
    tool = client.post(
        "/api/tools-workspace/tools",
        headers=auth,
        json={"register_number": "T-SPEC", "name": "Torque wrench", "storage_location": "Workshop", "specifications": {"Range": "40–200 Nm"}},
    ).json()

    updated = client.patch(
        f"/api/tools-workspace/tools/{tool['id']}",
        headers=auth,
        json={"specifications": {"Range": "40–200 Nm", "Drive": "1/2 inch"}},
    )
    assert updated.status_code == 200
    assert updated.json()["specifications"] == {"Range": "40–200 Nm", "Drive": "1/2 inch"}

    undone = client.post("/api/tools-workspace/changes/undo", headers=auth)
    assert undone.status_code == 200
    assert undone.json()["tool"]["specifications"] == {"Range": "40–200 Nm"}
    redone = client.post("/api/tools-workspace/changes/redo", headers=auth)
    assert redone.status_code == 200
    assert redone.json()["tool"]["specifications"] == {"Range": "40–200 Nm", "Drive": "1/2 inch"}


def test_transfer_extension_edit_archive_and_undo_are_durable():
    admin, token = admin_and_issuer()
    auth = {"Authorization": f"Bearer {token}"}
    admin_auth = {"Authorization": f"Bearer {admin}"}
    first = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-1", "name": "Alex", "department": "Engineering"}).json()
    second = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-2", "name": "Jordan", "department": "Engineering"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-9", "name": "Drill", "storage_location": "Workshop", "specifications": {"Voltage": "18 V"}}).json()
    assert client.patch(f"/api/tools-workspace/tools/{tool['id']}", headers=admin_auth, json={"name": "Cordless drill"}).status_code == 200
    assert client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=auth, json={"kind": "issue", "employee_id": first["id"], "location": "Plant", "assigned_equipment": ["Primary crusher"], "expected_return_at": "2030-01-02T10:00:00Z"}).status_code == 200
    assert client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=auth, json={"kind": "transfer", "employee_id": second["id"], "location": "Pit"}).status_code == 200
    assert client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=auth, json={"kind": "extend", "expected_return_at": "2030-01-03T10:00:00Z"}).status_code == 200
    returned = client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=auth, json={"kind": "return", "location": "Store", "condition": "Good"})
    assert returned.status_code == 200
    archived = client.post(f"/api/tools-workspace/tools/{tool['id']}/archive", headers=admin_auth)
    assert archived.status_code == 200 and archived.json()["archived"] is True
    undone = client.post("/api/tools-workspace/changes/undo", headers=admin_auth)
    assert undone.status_code == 200 and undone.json()["tool"]["archived"] is False
    redone = client.post("/api/tools-workspace/changes/redo", headers=admin_auth)
    assert redone.status_code == 200 and redone.json()["tool"]["archived"] is True


def test_registers_start_empty():
    token = register("first")
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/tools-workspace/tools", headers=auth).json() == []
    assert client.get("/api/tools-workspace/employees", headers=auth).json() == []
    assert client.get("/api/tools-workspace/history", headers=auth).json() == []


def test_usage_errors_and_feedback_are_available_to_analytics():
    token = register("observer")
    auth = {"Authorization": f"Bearer {token}"}
    viewer = register("analytics-viewer")
    assert client.post("/api/tools-workspace/analytics/usage", headers=auth, json={"event": "opened equipment register"}).status_code == 202
    assert client.post("/api/tools-workspace/analytics/errors", headers=auth, json={"message": "Example failure", "source": "prototype"}).status_code == 202
    assert client.post("/api/tools-workspace/feedback", headers=auth, data={"text": "Make search faster"}).status_code == 201
    data = client.get("/api/tools-workspace/analytics", headers=auth).json()
    assert data["totals"] == {"usage": 1, "errors": 1, "feedback": 1}
    assert client.get("/api/tools-workspace/analytics", headers={"Authorization": f"Bearer {viewer}"}).status_code == 403


def test_audio_feedback_normalizes_browser_codec_content_type():
    token = register("audio-admin")
    response = client.post(
        "/api/tools-workspace/feedback",
        headers={"Authorization": f"Bearer {token}"},
        files={"audio": ("feedback.webm", b"recorded-audio", "audio/webm;codecs=opus")},
    )
    assert response.status_code == 201
    assert response.json()["audio_content_type"] == "audio/webm"


def test_employee_register_preserves_supervisor_and_employee_number():
    token = register("people-admin")
    auth = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/tools-workspace/employees", headers=auth, json={"employee_number": "EMP-204", "name": "Rudo Moyo", "department": "Engineering", "supervisor_name": "C. Ncube"})
    assert created.status_code == 201
    assert created.json()["employee_number"] == "EMP-204"
    assert created.json()["supervisor_name"] == "C. Ncube"


def test_notification_reads_are_scoped_to_each_account():
    admin, issuer = admin_and_issuer()
    viewer = register("viewer", False)
    admin_auth = {"Authorization": f"Bearer {admin}"}
    viewer_auth = {"Authorization": f"Bearer {viewer}"}
    employee = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-7", "name": "Tariro", "department": "Engineering"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-7", "name": "Clamp meter", "storage_location": "Workshop"}).json()
    issuer_auth = {"Authorization": f"Bearer {issuer}"}
    client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers=issuer_auth, json={"employee_id": employee["id"], "location": "Plant"})
    client.post(f"/api/tools-workspace/tools/{tool['id']}/return", headers=issuer_auth, json={"location": "Tool room", "condition": "Damaged"})

    admin_alerts = client.get("/api/tools-workspace/notifications", headers=admin_auth).json()
    viewer_alerts = client.get("/api/tools-workspace/notifications", headers=viewer_auth).json()
    assert admin_alerts["unread_count"] == viewer_alerts["unread_count"] == 1

    key = admin_alerts["alerts"][0]["key"]
    assert client.post("/api/tools-workspace/notifications/read", headers=admin_auth, json={"keys": [key]}).status_code == 202
    assert client.get("/api/tools-workspace/notifications", headers=admin_auth).json()["unread_count"] == 0
    assert client.get("/api/tools-workspace/notifications", headers=viewer_auth).json()["unread_count"] == 1


def test_past_return_deadline_is_derived_as_overdue_without_a_scheduler():
    admin, issuer = admin_and_issuer()
    auth = {"Authorization": f"Bearer {admin}"}
    issuer_auth = {"Authorization": f"Bearer {issuer}"}
    employee = client.post("/api/tools-workspace/employees", headers=auth, json={"employee_number": "E-8", "name": "Rudo", "department": "Engineering"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=auth, json={"register_number": "T-8", "name": "Test meter", "storage_location": "Workshop"}).json()
    issued = client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers=issuer_auth, json={"employee_id": employee["id"], "location": "Plant", "expected_return_at": "2000-01-01T10:00:00Z"})
    assert issued.status_code == 200

    listed = client.get("/api/tools-workspace/tools", headers=auth).json()
    alerts = client.get("/api/tools-workspace/notifications", headers=auth).json()

    assert listed[0]["status"] == "overdue"
    assert alerts["unread_count"] == 1
    assert alerts["alerts"][0]["kind"] == "overdue"


def test_import_and_attachment_metadata_persist():
    token = register("storekeeper")
    auth = {"Authorization": f"Bearer {token}"}
    imported = client.post("/api/tools-workspace/imports/commit", headers=auth, json={"target": "equipment", "rows": [{"register_number": "I-1", "name": "Meter", "storage_location": "Store"}]})
    assert imported.status_code == 200 and imported.json()["accepted_count"] == 1
    tool = imported.json()["accepted"][0]
    attached = client.post(f"/api/tools-workspace/tools/{tool['id']}/evidence", headers=auth, files=[("files", ("condition.jpg", b"image-bytes", "image/jpeg"))])
    assert attached.status_code == 201
    reloaded = client.get("/api/tools-workspace/tools", headers=auth).json()
    assert reloaded[0]["evidence"][0]["original_name"] == "condition.jpg"


def test_signed_in_users_can_preserve_original_source_registers():
    token = register("records-clerk")
    auth = {"Authorization": f"Bearer {token}"}
    uploaded = client.post(
        "/api/tools-workspace/source-registers",
        headers=auth,
        data={"department": "Engineering", "notes": "Opening paper register scan"},
        files={"file": ("engineering-register.pdf", b"%PDF-example", "application/pdf")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["original_name"] == "engineering-register.pdf"
    assert uploaded.json()["uploaded_by"] == "Records-Clerk"

    listed = client.get("/api/tools-workspace/source-registers", headers=auth)
    assert listed.status_code == 200
    assert listed.json()[0]["department"] == "Engineering"
    assert listed.json()[0]["notes"] == "Opening paper register scan"

    rejected = client.post(
        "/api/tools-workspace/source-registers",
        headers=auth,
        data={"department": "Engineering"},
        files={"file": ("unsafe.exe", b"binary", "application/octet-stream")},
    )
    assert rejected.status_code == 415
