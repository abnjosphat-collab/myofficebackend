from fastapi.testclient import TestClient

from app.routers import tools_workspace
from main import app


client = TestClient(app)


class PagedResult:
    def __init__(self, data):
        self.data = data


class PagedQuery:
    def __init__(self, data):
        self.data = data
        self.window = (0, 999)
        self.orders = []

    def select(self, *_args): return self
    def order(self, column, desc=False): self.orders.append((column, desc)); return self
    def range(self, start, end): self.window = (start, end); return self
    def execute(self):
        start, end = self.window
        return PagedResult(self.data[start:end + 1])


class PagedSupabase:
    def __init__(self, data):
        self.query = PagedQuery(data)

    def table(self, _name): return self.query


def setup_function():
    tools_workspace._reset_for_tests()


def test_all_pages_large_registers_with_stable_ordering(monkeypatch):
    fake = PagedSupabase([{"id":str(index),"register_number":f"T-{index:04d}"} for index in range(1001)])
    monkeypatch.setattr(tools_workspace, "_test_mode", False)
    monkeypatch.setattr(tools_workspace, "supabase", fake)

    result = tools_workspace._all("tools")

    assert len(result) == 1001
    assert fake.query.orders == [("register_number", False), ("id", False)]
    assert fake.query.window == (1000, 1999)


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


def assign_viewer_department(admin_token: str, username: str, department: str = "Engineering"):
    admin_auth = {"Authorization": f"Bearer {admin_token}"}
    accounts = client.get("/api/tools-workspace/accounts", headers=admin_auth).json()
    account = next(item for item in accounts if item["username"] == username)
    response = client.patch(
        f"/api/tools-workspace/accounts/{account['id']}",
        headers=admin_auth,
        json={"role": "viewer", "department": department},
    )
    assert response.status_code == 200


def make_eligible_and_current(auth, employee, tool):
    competency = client.post(
        "/api/tools-workspace/competencies",
        headers=auth,
        json={"employee_id": employee["id"], "tool_id": tool["id"], "trained": True, "qualified": True, "authorized": True},
    )
    assert competency.status_code == 201
    for inspection_type in ("monthly", "quarterly"):
        inspection = client.post(
            f"/api/tools-workspace/tools/{tool['id']}/inspections",
            headers=auth,
            json={"inspection_type": inspection_type, "outcome": "passed", "next_due_at": "2035-01-01T00:00:00Z"},
        )
        assert inspection.status_code == 201


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
    make_eligible_and_current(admin_auth, employee, tool)
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
    make_eligible_and_current(admin_auth, employee, engineering_tool)

    denied_admin = client.post(f"/api/tools-workspace/tools/{engineering_tool['id']}/commands", headers=admin_auth, json={"kind": "issue", "employee_id": employee["id"], "location": "Plant"})
    denied_department = client.post(f"/api/tools-workspace/tools/{mining_tool['id']}/commands", headers=issuer_auth, json={"kind": "issue", "employee_id": employee["id"], "location": "Pit"})
    allowed = client.post(f"/api/tools-workspace/tools/{engineering_tool['id']}/commands", headers=issuer_auth, json={"kind": "issue", "employee_id": employee["id"], "location": "Plant", "assigned_equipment": ["Conveyor CV-01", "Pump P-12"], "pre_use_check_completed": True})

    assert denied_admin.status_code == 403
    assert denied_department.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["tool"]["custody"]["assigned_equipment"] == ["Conveyor CV-01", "Pump P-12"]


def test_repaired_equipment_can_be_marked_ready_with_an_audit_event():
    admin, issuer = admin_and_issuer("Engineering")
    viewer = register("viewer")
    assign_viewer_department(admin, "viewer")
    admin_auth = {"Authorization": f"Bearer {admin}"}
    issuer_auth = {"Authorization": f"Bearer {issuer}"}
    viewer_auth = {"Authorization": f"Bearer {viewer}"}
    employee = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-READY", "name": "Tafadzwa", "department": "Engineering"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-READY", "name": "Impact drill", "storage_location": "Workshop", "department": "Engineering"}).json()
    make_eligible_and_current(admin_auth, employee, tool)
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


def test_departmental_viewer_sees_only_assigned_department_registers():
    admin = register("scope-admin")
    engineering_viewer = register("engineering-viewer")
    admin_auth = {"Authorization": f"Bearer {admin}"}
    viewer_auth = {"Authorization": f"Bearer {engineering_viewer}"}
    accounts = client.get("/api/tools-workspace/accounts", headers=admin_auth).json()
    target = next(account for account in accounts if account["username"] == "engineering-viewer")

    updated = client.patch(
        f"/api/tools-workspace/accounts/{target['id']}",
        headers=admin_auth,
        json={"role": "viewer", "department": "Engineering"},
    )
    assert updated.status_code == 200
    assert updated.json()["department"] == "Engineering"

    client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-ENG", "name": "Eng Person", "department": "Engineering"})
    client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-MIN", "name": "Mining Person", "department": "Mining"})
    client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "ENG-1", "name": "Engineering drill", "storage_location": "Workshop", "department": "Engineering"})
    client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "MIN-1", "name": "Mining lamp", "storage_location": "Lamp room", "department": "Mining"})

    tools = client.get("/api/tools-workspace/tools", headers=viewer_auth).json()
    employees = client.get("/api/tools-workspace/employees", headers=viewer_auth).json()

    assert [tool["register_number"] for tool in tools] == ["ENG-1"]
    assert [employee["employee_number"] for employee in employees] == ["E-ENG"]


def test_unassigned_viewer_cannot_read_every_department():
    admin = register("unassigned-admin")
    viewer = register("unassigned-viewer")
    admin_auth = {"Authorization": f"Bearer {admin}"}
    viewer_auth = {"Authorization": f"Bearer {viewer}"}

    client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-PRIVATE", "name": "Private Person", "department": "Engineering"})
    client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "PRIVATE-1", "name": "Private tool", "storage_location": "Workshop", "department": "Engineering"})

    assert client.get("/api/tools-workspace/tools", headers=viewer_auth).json() == []
    assert client.get("/api/tools-workspace/employees", headers=viewer_auth).json() == []
    assert client.get("/api/tools-workspace/history", headers=viewer_auth).json() == []


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
    make_eligible_and_current(admin_auth, first, tool)
    make_eligible_and_current(admin_auth, second, tool)
    assert client.patch(f"/api/tools-workspace/tools/{tool['id']}", headers=admin_auth, json={"name": "Cordless drill"}).status_code == 200
    assert client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=auth, json={"kind": "issue", "employee_id": first["id"], "location": "Plant", "assigned_equipment": ["Primary crusher"], "expected_return_at": "2030-01-02T10:00:00Z", "pre_use_check_completed": True}).status_code == 200
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
    assign_viewer_department(admin, "viewer")
    admin_auth = {"Authorization": f"Bearer {admin}"}
    viewer_auth = {"Authorization": f"Bearer {viewer}"}
    employee = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-7", "name": "Tariro", "department": "Engineering"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-7", "name": "Clamp meter", "storage_location": "Workshop"}).json()
    make_eligible_and_current(admin_auth, employee, tool)
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
    make_eligible_and_current(auth, employee, tool)
    issued = client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers=issuer_auth, json={"employee_id": employee["id"], "location": "Plant", "expected_return_at": "2000-01-01T10:00:00Z"})
    assert issued.status_code == 200

    listed = client.get("/api/tools-workspace/tools", headers=auth).json()
    alerts = client.get("/api/tools-workspace/notifications", headers=auth).json()

    assert listed[0]["status"] == "overdue"
    assert alerts["unread_count"] == 1
    assert alerts["alerts"][0]["kind"] == "overdue_6h"


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


def test_generated_register_number_uses_sop_prefix_and_sequence():
    token = register("number-admin")
    auth = {"Authorization": f"Bearer {token}"}
    first = client.post(
        "/api/tools-workspace/tools",
        headers=auth,
        json={"name": "Angle grinder", "equipment_kind": "angle-grinder", "storage_location": "Main tool room", "department": "Engineering"},
    )
    second = client.post(
        "/api/tools-workspace/tools",
        headers=auth,
        json={"name": "Angle grinder", "equipment_kind": "angle-grinder", "storage_location": "Main tool room", "department": "Engineering"},
    )

    assert first.status_code == second.status_code == 201
    assert first.json()["register_number"] == "PP-UG-ENG-AG-01"
    assert second.json()["register_number"] == "PP-UG-ENG-AG-02"


def test_issue_requires_current_competency_checks_and_pre_use_confirmation():
    admin, issuer = admin_and_issuer()
    admin_auth = {"Authorization": f"Bearer {admin}"}
    issuer_auth = {"Authorization": f"Bearer {issuer}"}
    employee = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-COMP", "name": "Competent User", "department": "Engineering"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-COMP", "name": "Grinder", "storage_location": "Store"}).json()

    no_competency = client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=issuer_auth, json={"kind": "issue", "employee_id": employee["id"], "location": "Plant", "pre_use_check_completed": True})
    make_eligible_and_current(admin_auth, employee, tool)
    no_pre_use = client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=issuer_auth, json={"kind": "issue", "employee_id": employee["id"], "location": "Plant"})
    issued = client.post(f"/api/tools-workspace/tools/{tool['id']}/commands", headers=issuer_auth, json={"kind": "issue", "employee_id": employee["id"], "location": "Plant", "pre_use_check_completed": True})

    assert no_competency.status_code == 409
    assert "trained, qualified and authorized" in no_competency.json()["detail"]
    assert no_pre_use.status_code == 409
    assert issued.status_code == 200
    assert any(item["inspection_type"] == "pre_use" for item in client.get("/api/tools-workspace/compliance", headers=admin_auth).json()["inspections"])


def test_quick_competency_update_preserves_certificate_and_expiry_details():
    token = register("competency-admin")
    auth = {"Authorization": f"Bearer {token}"}
    employee = client.post("/api/tools-workspace/employees", headers=auth, json={"employee_number": "E-SAFE", "name": "Safe User", "department": "Engineering"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=auth, json={"register_number": "T-SAFE", "name": "Test meter", "storage_location": "Store"}).json()
    detailed = client.post(
        "/api/tools-workspace/competencies",
        headers=auth,
        json={
            "employee_id": employee["id"],
            "tool_id": tool["id"],
            "trained": True,
            "qualified": True,
            "authorized": True,
            "training_certificate_ref": "CERT-42",
            "training_expires_at": "2035-01-01T00:00:00Z",
            "qualification_ref": "TRADE-7",
            "notes": "Assessed in the workshop",
        },
    )

    quick_update = client.post(
        "/api/tools-workspace/competencies",
        headers=auth,
        json={
            "employee_id": employee["id"],
            "tool_id": tool["id"],
            "trained": True,
            "qualified": True,
            "authorized": False,
        },
    )

    assert detailed.status_code == quick_update.status_code == 201
    assert quick_update.json()["training_certificate_ref"] == "CERT-42"
    assert quick_update.json()["training_expires_at"] == "2035-01-01T00:00:00Z"
    assert quick_update.json()["qualification_ref"] == "TRADE-7"
    assert quick_update.json()["notes"] == "Assessed in the workshop"
    assert quick_update.json()["authorized"] is False


def test_failed_quarterly_inspection_quarantines_tool_and_uses_red_code():
    token = register("inspection-admin")
    auth = {"Authorization": f"Bearer {token}"}
    tool = client.post("/api/tools-workspace/tools", headers=auth, json={"register_number": "T-INSP", "name": "Torque wrench", "storage_location": "Store"}).json()

    result = client.post(
        f"/api/tools-workspace/tools/{tool['id']}/inspections",
        headers=auth,
        json={"inspection_type": "quarterly", "outcome": "failed", "defects": "Cracked handle"},
    )
    listed = client.get("/api/tools-workspace/tools", headers=auth).json()[0]

    assert result.status_code == 201
    assert result.json()["colour_code"] == "Red"
    assert listed["status"] == "attention"
    assert listed["condition"] == "Defective"


def test_repair_quote_records_the_sixty_percent_replacement_threshold():
    token = register("repair-admin")
    auth = {"Authorization": f"Bearer {token}"}
    tool = client.post("/api/tools-workspace/tools", headers=auth, json={"register_number": "T-REPAIR", "name": "Impact drill", "storage_location": "Store"}).json()

    within_limit = client.post(
        f"/api/tools-workspace/tools/{tool['id']}/inspections",
        headers=auth,
        json={"inspection_type": "repair", "outcome": "conditional", "repair_quote": 600, "new_equipment_price": 1000},
    )
    above_limit = client.post(
        f"/api/tools-workspace/tools/{tool['id']}/inspections",
        headers=auth,
        json={"inspection_type": "repair", "outcome": "conditional", "repair_quote": 601, "new_equipment_price": 1000},
    )

    assert within_limit.status_code == above_limit.status_code == 201
    assert within_limit.json()["repair_eligible"] is True
    assert above_limit.json()["repair_eligible"] is False


def test_incident_opens_twenty_four_hour_investigation_and_records_recovery():
    token = register("incident-admin")
    auth = {"Authorization": f"Bearer {token}"}
    tool = client.post("/api/tools-workspace/tools", headers=auth, json={"register_number": "T-LOSS", "name": "Laser level", "storage_location": "Survey store"}).json()

    reported = client.post(
        f"/api/tools-workspace/tools/{tool['id']}/incidents",
        headers=auth,
        json={"incident_type": "lost", "occurred_at": "2030-01-01T08:00:00Z", "explanation": "Equipment could not be located after the shift hand-back."},
    )
    assert reported.status_code == 201
    incident = reported.json()
    assert incident["status"] == "open"
    assert incident["investigation_due_at"]
    assert client.get("/api/tools-workspace/tools", headers=auth).json()[0]["status"] == "attention"

    missing_cost = client.post(
        f"/api/tools-workspace/incidents/{incident['id']}/close",
        headers=auth,
        json={"investigation_outcome": "Employee negligence was confirmed.", "negligence_confirmed": True},
    )
    closed = client.post(
        f"/api/tools-workspace/incidents/{incident['id']}/close",
        headers=auth,
        json={"investigation_outcome": "Employee negligence was confirmed.", "negligence_confirmed": True, "replacement_cost": 900},
    )

    assert missing_cost.status_code == 422
    assert closed.status_code == 200
    assert closed.json()["recovery_months"] == 6
    assert closed.json()["replacement_cost"] == 900


def test_contractor_equipment_requires_owner_and_preserves_oem_reference():
    token = register("contractor-admin")
    auth = {"Authorization": f"Bearer {token}"}
    rejected = client.post(
        "/api/tools-workspace/tools",
        headers=auth,
        json={"name": "Contractor grinder", "storage_location": "Entry quarantine", "ownership_type": "contractor"},
    )
    created = client.post(
        "/api/tools-workspace/tools",
        headers=auth,
        json={"name": "Contractor grinder", "storage_location": "Entry quarantine", "ownership_type": "contractor", "contractor_name": "ABC Mining", "oem_manual_ref": "OEM-AG-14"},
    )

    assert rejected.status_code == 422
    assert created.status_code == 201
    assert created.json()["contractor_name"] == "ABC Mining"
    assert created.json()["oem_manual_ref"] == "OEM-AG-14"


def test_external_gate_pass_requires_sequential_reauthenticated_signatures_and_pdf():
    token = register("gate-admin")
    auth = {"Authorization": f"Bearer {token}"}
    account = client.get("/api/tools-workspace/auth/me", headers=auth).json()
    roles = ["hos", "hod", "security", "finance", "general_manager"]
    updated = client.patch(f"/api/tools-workspace/accounts/{account['id']}", headers=auth, json={"role": "admin", "approval_roles": roles})
    assert updated.status_code == 200
    pin = client.put("/api/tools-workspace/auth/signing-pin", headers=auth, json={"password": "secret12", "pin": "4826"})
    assert pin.status_code == 200
    tool = client.post("/api/tools-workspace/tools", headers=auth, json={"register_number": "T-GATE", "name": "Survey instrument", "storage_location": "Survey store"}).json()
    created = client.post(
        "/api/tools-workspace/gate-passes",
        headers=auth,
        json={"movement_scope": "external", "department": "Engineering", "destination": "OEM workshop", "purpose": "Calibration", "expected_out_at": "2030-01-01T08:00:00Z", "expected_return_at": "2030-01-03T16:00:00Z", "finance_required": True, "tool_ids": [tool["id"]]},
    )
    assert created.status_code == 201
    gate_pass = created.json()
    assert [item["role"] for item in gate_pass["approvals"]] == roles

    for index, role in enumerate(roles):
        credential = "secret12" if index == 0 else "4826"
        decision = client.post(f"/api/tools-workspace/gate-passes/{gate_pass['id']}/decision", headers=auth, json={"decision": "approve", "credential": credential, "comment": f"Approved as {role}"})
        assert decision.status_code == 200
    assert decision.json()["status"] == "approved"
    assert all(item["signer_name"] == "Gate-Admin" for item in decision.json()["approvals"])

    pdf = client.get(f"/api/tools-workspace/gate-passes/{gate_pass['id']}/pdf", headers=auth)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
