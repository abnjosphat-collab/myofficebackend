# tests/test_breakdowns_heatmap.py — regression test for a real bug found during the
# 2026-07-30 convoluted-code audit: GET /api/breakdowns/analytics/heatmap referenced
# `dept`/`loc`/`artisan_name`/`resp_time`/`day_names` before they were assigned
# anywhere in the function (Python's function-wide variable scoping made this an
# UnboundLocalError on the very first record), silently turned into a generic 500 by
# the endpoint's broad except-Exception handler. Fixed by extracting those fields once
# up front and hoisting DAY_NAMES to module level. This test exists so that class of
# bug — "works with an empty table, crashes the moment real data exists" — can't
# silently regress; fake Supabase, no network.

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.breakdowns as bd
from app.auth import get_current_user


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def lte(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        return _Result(self._rows)


class _FakeSupabase:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _FakeQuery(self._rows)


# One record per breakdown-relevant field the endpoint reads, so any of them being
# used-before-assigned would trip the bug this test guards against.
SAMPLE_RECORDS = [
    {
        "breakdown_start": "09:15", "breakdown_date": "2026-07-28",
        "breakdown_type": "Mechanical", "department": "Milling", "priority": "high",
        "artisan_name": "T. Moyo", "location": "Bay 3", "machine_name": "Crusher 1",
        "response_time_minutes": 12, "downtime_minutes": 45, "repair_time_minutes": 30,
        "status": "resolved", "spares_used": "[]",
    },
    {
        "breakdown_start": "14:40", "breakdown_date": "2026-07-29",
        "breakdown_type": "Electrical", "department": "Crushing", "priority": "medium",
        "artisan_name": "R. Ncube", "location": "Bay 1", "machine_name": "Conveyor 2",
        "response_time_minutes": 8, "downtime_minutes": 20, "repair_time_minutes": 15,
        "status": "logged", "spares_used": "[]",
    },
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bd, "supabase", _FakeSupabase(SAMPLE_RECORDS))
    test_app = FastAPI()
    test_app.include_router(bd.router)
    test_app.dependency_overrides[get_current_user] = lambda: {"role": "manager", "email": "t@t"}
    yield TestClient(test_app)
    test_app.dependency_overrides.clear()


def test_heatmap_does_not_crash_on_real_data(client):
    resp = client.get("/api/breakdowns/analytics/heatmap")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["heatmap"]["labels"]["days"] == [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    ]
    assert len(body["heatmap"]["hour_day"]) == 24
    assert body["daily_distribution"][0]["day"] == "Monday"


def test_heatmap_handles_empty_table(client, monkeypatch):
    monkeypatch.setattr(bd, "supabase", _FakeSupabase([]))
    resp = client.get("/api/breakdowns/analytics/heatmap")
    assert resp.status_code == 200, resp.text
