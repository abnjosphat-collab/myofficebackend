# tests/test_maintenance_stats_and_ppe.py — maintenance.py's stats/dashboard endpoints
# and its own small, separate PPE-issue CRUD (get_ppe_records/create_ppe_record —
# distinct from app/routers/ppe.py's fuller PPE module) had zero tests. Covers
# get_work_order_stats' status/priority breakdown, overdue-count and average-progress
# calculations, get_maintenance_dashboard_stats' cross-table efficiency calc, and the
# maintenance.py-local PPE list/create endpoints.

import pytest

import app.routers.maintenance as m
from app.routers.maintenance import (
    get_work_order_stats, get_maintenance_dashboard_stats,
    get_ppe_records, create_ppe_record, PPEIssueCreate,
)
from fastapi import HTTPException


class _BrokenRedis:
    async def get(self, *a, **k): raise ConnectionError("no redis in tests")
    async def set(self, *a, **k): raise ConnectionError("no redis in tests")
    async def sadd(self, *a, **k): raise ConnectionError("no redis in tests")
    async def smembers(self, *a, **k): raise ConnectionError("no redis in tests")
    async def delete(self, *a, **k): raise ConnectionError("no redis in tests")


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    from app import cache as cache_mod
    monkeypatch.setattr(cache_mod, "redis_client", _BrokenRedis())


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """select-only chainable query; filters via eq, returns a table's canned rows.
    Good enough for the read-only stats endpoints under test here."""
    def __init__(self, table_name, state):
        self.table_name = table_name
        self.state = state
        self._filters = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def insert(self, data):
        self._insert_payload = data
        return self

    def execute(self):
        if hasattr(self, "_insert_payload"):
            table = self.state.setdefault(self.table_name, [])
            row = dict(self._insert_payload, id=len(table) + 1)
            table.append(row)
            return _Resp([row])
        rows = self.state.get(self.table_name, [])
        matches = [r for r in rows if all(r.get(c) == v for c, v in self._filters)]
        return _Resp(matches)


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(name, self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {}

    def _patch(work_orders=None, ppe_records=None):
        state["work_orders"] = list(work_orders or [])
        state["ppe_records"] = list(ppe_records or [])
        monkeypatch.setattr(m, "supabase", _FakeSupabase(state))
        return state

    return _patch


def _user():
    return {"user_id": "u-1", "email": "u@x.com", "role": "user"}


# ─── get_work_order_stats ───────────────────────────────────────────────────────────

async def test_stats_empty_table_is_all_zero(patch_supabase):
    patch_supabase(work_orders=[])
    stats = await get_work_order_stats()
    assert stats["total_records"] == 0
    assert stats["overdue_count"] == 0
    assert stats["average_progress"] == 0
    assert stats["pending"] == 0


async def test_stats_status_and_priority_breakdown(patch_supabase):
    patch_supabase(work_orders=[
        {"status": "pending", "priority": "urgent", "due_date": None, "progress": 0},
        {"status": "pending", "priority": "high", "due_date": None, "progress": 50},
        {"status": "completed", "priority": "low", "due_date": None, "progress": 100},
    ])
    stats = await get_work_order_stats()
    assert stats["total_records"] == 3
    assert stats["pending"] == 2
    assert stats["completed"] == 1
    assert stats["in_progress"] == 0
    assert stats["urgent"] == 1
    assert stats["high"] == 1
    assert stats["low"] == 1
    assert stats["status_breakdown"] == {"pending": 2, "completed": 1}
    assert stats["priority_breakdown"] == {"urgent": 1, "high": 1, "low": 1}


async def test_stats_overdue_counts_past_due_date_excluding_completed(patch_supabase):
    patch_supabase(work_orders=[
        {"status": "pending", "priority": "low", "due_date": "2020-01-01", "progress": 0},   # overdue
        {"status": "completed", "priority": "low", "due_date": "2020-01-01", "progress": 100},  # completed -> not overdue
        {"status": "pending", "priority": "low", "due_date": "2099-01-01", "progress": 0},   # future -> not overdue
        {"status": "pending", "priority": "low", "due_date": None, "progress": 0},           # no due date -> not overdue
    ])
    stats = await get_work_order_stats()
    assert stats["overdue_count"] == 1


async def test_stats_overdue_skips_malformed_due_date(patch_supabase):
    patch_supabase(work_orders=[
        {"status": "pending", "priority": "low", "due_date": "not-a-date", "progress": 0},
    ])
    stats = await get_work_order_stats()
    assert stats["overdue_count"] == 0  # malformed date doesn't crash or count


async def test_stats_average_progress_rounds(patch_supabase):
    patch_supabase(work_orders=[
        {"status": "pending", "priority": "low", "due_date": None, "progress": 10},
        {"status": "pending", "priority": "low", "due_date": None, "progress": 25},
        {"status": "pending", "priority": "low", "due_date": None, "progress": None},  # excluded from average
    ])
    stats = await get_work_order_stats()
    assert stats["average_progress"] == round((10 + 25) / 2)  # None progress doesn't count toward denominator


# ─── get_maintenance_dashboard_stats ────────────────────────────────────────────────

async def test_dashboard_combines_work_orders_and_ppe_with_efficiency(patch_supabase):
    patch_supabase(
        work_orders=[
            {"status": "completed", "priority": "low", "due_date": None, "progress": 100},
            {"status": "completed", "priority": "low", "due_date": None, "progress": 100},
            {"status": "pending", "priority": "low", "due_date": None, "progress": 0},
        ],
        ppe_records=[{"id": 1}, {"id": 2}],
    )
    result = await get_maintenance_dashboard_stats()
    assert result["ppe_count"] == 2
    assert result["work_orders"]["total_records"] == 3
    assert result["overall_efficiency"] == round(2 / 3 * 100)  # 2 completed of 3 total
    assert result["total_maintenance_items"] == 3 + 2


async def test_dashboard_zero_work_orders_efficiency_is_zero_not_a_crash(patch_supabase):
    patch_supabase(work_orders=[], ppe_records=[])
    result = await get_maintenance_dashboard_stats()
    assert result["overall_efficiency"] == 0
    assert result["total_maintenance_items"] == 0


# ─── get_ppe_records (maintenance.py's own PPE endpoints) ──────────────────────────

async def test_get_ppe_records_filters_by_status(patch_supabase):
    patch_supabase(ppe_records=[
        {"id": 1, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "Site A", "employee_id": "E1"},
        {"id": 2, "status": "expired", "ppe_type": "helmet", "department": "Ops",
         "location": "Site A", "employee_id": "E1"},
    ])
    result = await get_ppe_records(status="active", ppe_type=None, department=None, location=None, employee_id=None)
    assert [r["id"] for r in result] == [1]


async def test_get_ppe_records_no_filters_returns_all(patch_supabase):
    patch_supabase(ppe_records=[
        {"id": 1, "status": "active", "ppe_type": "helmet", "department": "Ops",
         "location": "Site A", "employee_id": "E1"},
        {"id": 2, "status": "expired", "ppe_type": "gloves", "department": "Eng",
         "location": "Site B", "employee_id": "E2"},
    ])
    result = await get_ppe_records(status=None, ppe_type=None, department=None, location=None, employee_id=None)
    assert len(result) == 2


# ─── create_ppe_record (maintenance.py's own PPE endpoints) ────────────────────────

async def test_create_ppe_record_happy_path(patch_supabase):
    patch_supabase()
    record = PPEIssueCreate(
        employee_name="J. Doe", employee_id="E1", department="Ops", position="Fitter",
        ppe_type="helmet", item_name="Hard Hat", issue_date="2026-08-01",
    )
    result = await create_ppe_record(record, current_user=_user())
    assert result["employee_name"] == "J. Doe"
    assert result["issue_date"] == "2026-08-01"
    assert "created_at" in result


async def test_create_ppe_record_with_expiry_date_isoformats_it(patch_supabase):
    patch_supabase()
    record = PPEIssueCreate(
        employee_name="J. Doe", employee_id="E1", department="Ops", position="Fitter",
        ppe_type="helmet", item_name="Hard Hat", issue_date="2026-08-01", expiry_date="2027-08-01",
    )
    result = await create_ppe_record(record, current_user=_user())
    assert result["expiry_date"] == "2027-08-01"
