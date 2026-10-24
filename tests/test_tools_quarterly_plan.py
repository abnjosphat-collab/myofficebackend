"""Quarterly inspections on a calendar plan (Part 3): the window, generation, due logic and recording."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.rate_limit import limiter
from app.routers import tools_workspace
from app.zim_holidays import zim_holiday_name
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
    issuer = register("quarter-issuer")
    accounts = client.get("/api/tools-workspace/accounts", headers={"Authorization": f"Bearer {admin}"}).json()
    target = next(account for account in accounts if account["username"] == "quarter-issuer")
    promoted = client.patch(f"/api/tools-workspace/accounts/{target['id']}", headers={"Authorization": f"Bearer {admin}"}, json={"role": "issuer", "department": department})
    assert promoted.status_code == 200
    return {"Authorization": f"Bearer {admin}"}, {"Authorization": f"Bearer {issuer}"}


def seed_tool(index, location="Bay 0", **over):
    row = {"id": f"t{index}", "register_number": f"T-{index:03d}" if isinstance(index, int) else f"T-{index}", "name": f"Tool {index}", "storage_location": location,
           "department": "Engineering", "archived": False, "quarterly_inspection_required": True}
    row.update(over)
    return tools_workspace._insert("tools", row)


# --- the window: first 10 working days of the quarter, holidays out ---

def test_window_skips_weekends_and_zimbabwe_holidays():
    days = tools_workspace._quarter_window(2026, 1)
    assert len(days) == 10
    assert days[0] == "2026-01-02"  # New Year's Day (Thursday) is not a working day
    assert "2026-01-01" not in days
    for day in days:
        assert date.fromisoformat(day).weekday() < 5
        assert zim_holiday_name(day) is None
    assert days == sorted(days)


def test_window_starting_on_a_weekend_begins_on_monday():
    days = tools_workspace._quarter_window(2022, 1)  # 1 January 2022 was a Saturday
    assert len(days) == 10
    assert days[0] == "2022-01-03"


# --- generation: even spread, locations kept together, once only ---

def test_100_tools_spread_evenly_with_locations_kept_together():
    for index in range(100):
        seed_tool(index, location=f"Bay {index % 10}")
    rows = tools_workspace._ensure_quarter_plan(2026, 4)
    assert len(rows) == 100
    by_day = {}
    for row in rows:
        by_day.setdefault(row["planned_date"], []).append(row)
    assert len(by_day) == 10
    assert sorted(len(group) for group in by_day.values()) == [10] * 10
    tools = {tool["id"]: tool for tool in tools_workspace._all("tools")}
    location_days = {}
    for row in rows:
        location_days.setdefault(tools[row["tool_id"]]["storage_location"], set()).add(row["planned_date"])
    assert all(len(days) == 1 for days in location_days.values())
    assert {row["quarter"] for row in rows} == {"2026-Q4"}
    assert all(row["completed_inspection_id"] is None for row in rows)


def test_generation_is_idempotent_and_skips_archived_and_non_quarterly_tools():
    for index in range(5):
        seed_tool(index)
    seed_tool("arch", archived=True)
    seed_tool("noq", quarterly_inspection_required=False)
    first = tools_workspace._ensure_quarter_plan(2026, 4)
    assert len(first) == 5
    second = tools_workspace._ensure_quarter_plan(2026, 4)
    assert [row["id"] for row in second] == [row["id"] for row in first]


def test_tool_added_mid_window_goes_to_the_next_free_day():
    for index in range(10):
        seed_tool(index, location=f"Bay {index}")
    window = tools_workspace._quarter_window(2026, 4)
    today = date.fromisoformat(window[2])
    tools_workspace._ensure_quarter_plan(2026, 4, today)
    seed_tool("late", location="Bay 0")
    rows = tools_workspace._ensure_quarter_plan(2026, 4, today)
    late = next(row for row in rows if row["tool_id"] == "tlate")
    assert late["quarter"] == "2026-Q4"
    assert late["planned_date"] == window[2]  # every day held one; the earliest day still open wins


def test_tool_added_after_the_window_goes_to_next_quarter():
    for index in range(3):
        seed_tool(index)
    window = tools_workspace._quarter_window(2026, 4)
    tools_workspace._ensure_quarter_plan(2026, 4, date.fromisoformat(window[0]))
    seed_tool("late")
    rows = tools_workspace._ensure_quarter_plan(2026, 4, date(2026, 12, 31))
    assert len([row for row in rows if row["quarter"] == "2026-Q4"]) == 3
    coming = [row for row in tools_workspace._all("plan") if row["quarter"] == "2027-Q1"]
    assert len(coming) == 1
    assert coming[0]["tool_id"] == "tlate"
    assert coming[0]["planned_date"] == tools_workspace._quarter_window(2027, 1)[0]


# --- due logic: on the planned date, overdue past the window, cleared by a pass ---

def test_quarterly_due_around_the_planned_date_and_window_edges():
    for index in range(10):
        seed_tool(index, location=f"Bay {index}")
    window = tools_workspace._quarter_window(2026, 4)
    tools_workspace._ensure_quarter_plan(2026, 4, date.fromisoformat(window[0]))
    planned = date.fromisoformat(window[5])
    tool_id = next(row["tool_id"] for row in tools_workspace._all("plan") if row["planned_date"] == window[5])
    tool = tools_workspace._find("tools", "id", tool_id)
    assert tools_workspace._quarterly_due(tool, [], planned - timedelta(days=1)) is False
    assert tools_workspace._quarterly_due(tool, [], planned) is True
    assert tools_workspace._quarterly_due(tool, [], date.fromisoformat(window[-1]) + timedelta(days=30)) is True


def test_pass_recorded_in_the_quarter_clears_the_due_but_last_quarter_does_not():
    seed_tool(0)
    window = tools_workspace._quarter_window(2026, 4)
    tools_workspace._ensure_quarter_plan(2026, 4, date.fromisoformat(window[-1]))
    tool = tools_workspace._find("tools", "id", "t0")
    today = date.fromisoformat(window[-1])
    current = [{"tool_id": "t0", "inspection_type": "quarterly", "outcome": "passed", "inspected_at": f"{window[0]}T10:00:00+00:00"}]
    assert tools_workspace._quarterly_due(tool, current, today) is False
    stale = [{"tool_id": "t0", "inspection_type": "quarterly", "outcome": "passed", "inspected_at": "2026-09-15T10:00:00+00:00"}]
    assert tools_workspace._quarterly_due(tool, stale, today) is True
    failed = [{"tool_id": "t0", "inspection_type": "quarterly", "outcome": "failed", "inspected_at": f"{window[0]}T10:00:00+00:00"}]
    assert tools_workspace._quarterly_due(tool, failed, today) is True


# --- API wiring ---

def test_compliance_carries_the_plan_and_recording_completes_it_with_next_quarter_due():
    admin_auth, issuer_auth = admin_and_issuer()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-Q", "name": "Grinder", "storage_location": "Workshop"}).json()
    plan = client.get("/api/tools-workspace/compliance", headers=admin_auth).json()["inspection_plan"]
    assert len(plan) == 1 and plan[0]["tool_id"] == tool["id"] and plan[0]["completed_inspection_id"] is None
    recorded = client.post(f"/api/tools-workspace/tools/{tool['id']}/inspections", headers=issuer_auth, json={"inspection_type": "quarterly", "outcome": "passed"}).json()
    year, quarter = tools_workspace._quarter_of(date.today())
    next_year, next_quarter = tools_workspace._next_quarter(year, quarter)
    expected = tools_workspace._quarter_window(next_year, next_quarter)[0] + "T00:00:00+00:00"
    assert recorded["next_due_at"] == expected
    done = client.get("/api/tools-workspace/compliance", headers=admin_auth).json()["inspection_plan"]
    assert next(row for row in done if row["tool_id"] == tool["id"])["completed_inspection_id"] == recorded["id"]
    listed = client.get("/api/tools-workspace/tools", headers=admin_auth).json()
    assert "quarterly" not in next(item for item in listed if item["id"] == tool["id"])["inspection_due"]


def test_failed_quarterly_does_not_complete_the_plan_row():
    admin_auth, issuer_auth = admin_and_issuer()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-QF", "name": "Drill", "storage_location": "Workshop"}).json()
    client.get("/api/tools-workspace/compliance", headers=admin_auth)
    client.post(f"/api/tools-workspace/tools/{tool['id']}/inspections", headers=issuer_auth, json={"inspection_type": "quarterly", "outcome": "failed", "defects": "Cracked housing"})
    plan = client.get("/api/tools-workspace/compliance", headers=admin_auth).json()["inspection_plan"]
    assert next(row for row in plan if row["tool_id"] == tool["id"])["completed_inspection_id"] is None


def test_new_tools_default_to_no_monthly_inspection_but_keep_quarterly():
    admin_auth, _ = admin_and_issuer()
    tool = client.post("/api/tools-workspace/tools", headers=admin_auth, json={"register_number": "T-NM", "name": "Saw", "storage_location": "Workshop"}).json()
    assert tool["monthly_inspection_required"] is False
    assert tool["quarterly_inspection_required"] is True
