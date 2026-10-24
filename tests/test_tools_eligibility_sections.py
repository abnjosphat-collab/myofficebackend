"""Eligibility by trade section (Part 2): the classifier, the _is_eligible rule and its API wiring."""
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


def register(username: str):
    response = client.post("/api/tools-workspace/auth/register", json={"name": username.title(), "username": username, "password": "secret12"})
    assert response.status_code == 201
    return response.json()["token"]


def admin_and_issuer(department: str = "Engineering"):
    admin = register("admin")
    issuer = register("section-issuer")
    accounts = client.get("/api/tools-workspace/accounts", headers={"Authorization": f"Bearer {admin}"}).json()
    target = next(account for account in accounts if account["username"] == "section-issuer")
    promoted = client.patch(f"/api/tools-workspace/accounts/{target['id']}", headers={"Authorization": f"Bearer {admin}"}, json={"role": "issuer", "department": department})
    assert promoted.status_code == 200
    return {"Authorization": f"Bearer {admin}"}, {"Authorization": f"Bearer {issuer}"}


def employee_row(section="Electrical", job_title="Electrician", active=True, employee_id="e1"):
    return {"id": employee_id, "section": section, "job_title": job_title, "active": active}


def tool_row(trade_sections=("Electrical",), tool_id="t1", competency_required=True):
    return {"id": tool_id, "trade_sections": list(trade_sections), "competency_required": competency_required}


def current_record(employee_id="e1", tool_id="t1", category=None):
    return {"employee_id": employee_id, "tool_id": tool_id, "category": category, "trained": True, "qualified": True, "authorized": True,
            "training_expires_at": None, "qualification_expires_at": None, "authorization_expires_at": None}


# --- the classifier is pure: name, equipment kind and category in, trade sections out ---

def test_classifier_suggests_electrical_for_test_instruments():
    assert tools_workspace.suggest_trade_sections("Digital multimeter", "digital-multimeter", "Test & measure") == ["Electrical"]
    assert tools_workspace.suggest_trade_sections("Clamp meter", "clamp-meter", "Test & measure") == ["Electrical"]
    assert tools_workspace.suggest_trade_sections("Extension cable reel", "other-equipment", "Electrical equipment") == ["Electrical"]


def test_classifier_suggests_mechanical_for_hand_tools_and_lifting():
    assert tools_workspace.suggest_trade_sections("Torque wrench", "torque-wrench", "Hand tools") == ["Mechanical"]
    assert tools_workspace.suggest_trade_sections("Chain block 2t", "lifting-equipment", "Lifting equipment") == ["Mechanical"]


def test_classifier_suggests_both_trades_for_shared_power_tools():
    assert tools_workspace.suggest_trade_sections("Angle grinder", "angle-grinder", "Power tools") == ["Electrical", "Mechanical"]
    assert tools_workspace.suggest_trade_sections("Inverter welder", "inverter-welder", "Welding") == ["Electrical", "Mechanical"]


def test_classifier_suggests_neither_trade_outside_engineering_trades():
    assert tools_workspace.suggest_trade_sections("Total station", "survey-equipment", "Surveying") == []
    assert tools_workspace.suggest_trade_sections("Site laptop", "laptop", "IT equipment") == []


def test_classifier_catches_real_register_namings():
    assert tools_workspace.suggest_trade_sections("Fluke test leads", "other-equipment", "") == ["Electrical"]
    assert tools_workspace.suggest_trade_sections("Precision Screw Driver Set", "other-equipment", "") == ["Electrical", "Mechanical"]
    assert tools_workspace.suggest_trade_sections("Micrometer Screwguage set", "other-equipment", "") == ["Mechanical"]
    assert tools_workspace.suggest_trade_sections("20m tape measure", "other-equipment", "") == ["Electrical", "Mechanical"]
    assert tools_workspace.suggest_trade_sections("Oxy acetylene set", "other-equipment", "") == ["Electrical", "Mechanical"]
    assert tools_workspace.suggest_trade_sections("Plastic Pipe Welder", "other-equipment", "") == ["Mechanical"]
    assert tools_workspace.suggest_trade_sections("Impact wrench", "other-equipment", "") == ["Mechanical"]


def test_classifier_name_beats_a_vague_category():
    assert tools_workspace.suggest_trade_sections("Angle grinder", "other-equipment", "Other equipment") == ["Electrical", "Mechanical"]
    assert tools_workspace.suggest_trade_sections("Digital multimeter", "other-equipment", "") == ["Electrical"]


# --- the rule, in one place ---

def test_section_member_is_eligible_for_their_trade_tool():
    assert tools_workspace._is_eligible(employee_row(), tool_row(), []) is True
    assert tools_workspace._eligibility_basis(employee_row(), tool_row(), []) == "section:Electrical"


def test_mechanical_member_is_eligible_for_a_mechanical_tool():
    employee = employee_row(section="Mechanical", job_title="Fitter")
    assert tools_workspace._is_eligible(employee, tool_row(("Mechanical",)), []) is True


def test_either_trade_is_eligible_for_a_shared_tool():
    tool = tool_row(("Electrical", "Mechanical"))
    assert tools_workspace._is_eligible(employee_row(), tool, []) is True
    assert tools_workspace._is_eligible(employee_row(section="Mechanical", job_title="Boilermaker"), tool, []) is True


def test_driver_in_the_section_is_not_eligible():
    assert tools_workspace._is_eligible(employee_row(job_title="Tipper Driver"), tool_row(), []) is False
    assert tools_workspace._is_eligible(employee_row(job_title="DRIVER"), tool_row(), []) is False


def test_lamp_room_attendant_in_the_section_is_not_eligible():
    assert tools_workspace._is_eligible(employee_row(job_title="Lamp Room Attendant"), tool_row(), []) is False


def test_explicit_removal_beats_the_section_rule():
    removal = {"employee_id": "e1", "tool_id": "t1", "category": None, "trained": False, "qualified": False, "authorized": False,
               "training_expires_at": None, "qualification_expires_at": None, "authorization_expires_at": None}
    assert tools_workspace._is_eligible(employee_row(), tool_row(), [removal]) is False


def test_individual_approval_outside_the_section_is_eligible():
    employee = employee_row(section="Mechanical", job_title="Fitter")
    assert tools_workspace._is_eligible(employee, tool_row(), [current_record()]) is True
    assert tools_workspace._eligibility_basis(employee, tool_row(), [current_record()]) == "individual"


def test_individual_approval_wins_over_the_section_basis():
    assert tools_workspace._eligibility_basis(employee_row(), tool_row(), [current_record()]) == "individual"


def test_inactive_section_member_is_not_eligible():
    assert tools_workspace._is_eligible(employee_row(active=False), tool_row(), []) is False


def test_lapsed_approval_falls_back_to_the_section():
    expired = {**current_record(), "authorization_expires_at": "2000-01-01T00:00:00Z"}
    assert tools_workspace._eligibility_basis(employee_row(), tool_row(), [expired]) == "section:Electrical"


def test_category_approval_still_counts_as_individual():
    assert tools_workspace._eligibility_basis(employee_row(section=None), tool_row(), [current_record(category="Test & measure")]) == "individual"


def test_tool_without_trade_sections_keeps_individual_only():
    assert tools_workspace._is_eligible(employee_row(), tool_row(()), []) is False
    assert tools_workspace._is_eligible(employee_row(), {"id": "t1", "competency_required": True}, []) is False


def test_tool_anyone_may_use_stays_open():
    assert tools_workspace._eligibility_basis(employee_row(section=None), tool_row((), competency_required=False), []) == "open"


# --- API wiring ---

def make_people_and_tool(admin_auth):
    electrician = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-11", "name": "Anesu", "department": "Engineering", "job_title": "Electrician", "section": "Electrical"}).json()
    driver = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-12", "name": "Blessing", "department": "Engineering", "job_title": "Driver", "section": "Electrical"}).json()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-SEC", "name": "Clamp meter", "storage_location": "Workshop", "category": "Test & measure", "trade_sections": ["Electrical"], "monthly_inspection_required": False, "quarterly_inspection_required": False}).json()
    assert electrician["section"] == "Electrical"
    assert tool["trade_sections"] == ["Electrical"]
    return electrician, driver, tool


def eligible_for(admin_auth, tool_id):
    listed = client.get("/api/tools-workspace/tools", headers=admin_auth).json()
    return next(item for item in listed if item["id"] == tool_id)["eligible_employees"]


def test_eligible_list_marks_the_section_basis():
    admin_auth, _ = admin_and_issuer()
    electrician, _, tool = make_people_and_tool(admin_auth)
    entries = eligible_for(admin_auth, tool["id"])
    assert [(entry["id"], entry["via_section"]) for entry in entries] == [(electrician["id"], "Electrical")]


def test_section_member_can_be_issued_and_driver_cannot():
    admin_auth, issuer_auth = admin_and_issuer()
    electrician, driver, tool = make_people_and_tool(admin_auth)
    ok = client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers=issuer_auth, json={"employee_id": electrician["id"], "location": "Plant"})
    assert ok.status_code == 200
    client.post(f"/api/tools-workspace/tools/{tool['id']}/return", headers=issuer_auth, json={"location": "Workshop", "condition": "Good"})
    refused = client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers=issuer_auth, json={"employee_id": driver["id"], "location": "Plant"})
    assert refused.status_code == 409


def test_removing_a_section_member_writes_a_record_that_sticks():
    admin_auth, issuer_auth = admin_and_issuer()
    electrician, _, tool = make_people_and_tool(admin_auth)
    removed = client.post("/api/tools-workspace/competencies", headers=admin_auth, json={"employee_id": electrician["id"], "tool_id": tool["id"], "trained": False, "qualified": False, "authorized": False})
    assert removed.status_code == 201
    assert eligible_for(admin_auth, tool["id"]) == []
    refused = client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers=issuer_auth, json={"employee_id": electrician["id"], "location": "Plant"})
    assert refused.status_code == 409


def test_individual_approval_covers_someone_outside_the_section():
    admin_auth, issuer_auth = admin_and_issuer()
    _, _, tool = make_people_and_tool(admin_auth)
    fitter = client.post("/api/tools-workspace/employees", headers=admin_auth, json={"employee_number": "E-13", "name": "Chipo", "department": "Engineering", "job_title": "Fitter", "section": "Mechanical"}).json()
    assert fitter["id"] not in [entry["id"] for entry in eligible_for(admin_auth, tool["id"])]
    granted = client.post("/api/tools-workspace/competencies", headers=admin_auth, json={"employee_id": fitter["id"], "tool_id": tool["id"], "trained": True, "qualified": True, "authorized": True})
    assert granted.status_code == 201
    entries = eligible_for(admin_auth, tool["id"])
    assert next(entry for entry in entries if entry["id"] == fitter["id"])["via_section"] is None
    issued = client.post(f"/api/tools-workspace/tools/{tool['id']}/issue", headers=issuer_auth, json={"employee_id": fitter["id"], "location": "Plant"})
    assert issued.status_code == 200


def test_changing_section_changes_eligibility():
    admin_auth, _ = admin_and_issuer()
    electrician, _, tool = make_people_and_tool(admin_auth)
    assert [entry["id"] for entry in eligible_for(admin_auth, tool["id"])] == [electrician["id"]]
    moved = client.patch(f"/api/tools-workspace/employees/{electrician['id']}", headers=admin_auth, json={"section": "Mechanical"})
    assert moved.status_code == 200
    assert eligible_for(admin_auth, tool["id"]) == []
    cleared = client.patch(f"/api/tools-workspace/employees/{electrician['id']}", headers=admin_auth, json={"section": None})
    assert cleared.status_code == 200
    assert cleared.json()["section"] is None


def test_suggestion_endpoint_matches_the_classifier():
    admin_auth, _ = admin_and_issuer()
    response = client.get("/api/tools-workspace/tools/trade-section-suggestion", params={"name": "Angle grinder", "equipment_kind": "angle-grinder", "category": "Power tools"}, headers=admin_auth)
    assert response.status_code == 200
    assert response.json() == {"trade_sections": ["Electrical", "Mechanical"]}
