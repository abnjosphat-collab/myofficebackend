# tests/test_daily_reports_crud.py — health_check, get_reports, create_report,
# delete_all_reports, export_to_excel, export_to_pdf.
#
# This router builds its own module-level `supabase` client (not the shared
# app.supabase_client one) and treats it as an optional dependency: every handler
# has an explicit `if not supabase:` branch that returns canned/sample data instead
# of talking to a database — a deliberate "local mode" fallback (unlike the
# already-fixed fake-200-on-exception anti-pattern in test_daily_reports_trends.py,
# this is a documented, intentional no-DB-configured mode, not a caught failure).
# Both the "with supabase" and "local mode" paths are exercised below since they're
# genuinely different code paths, not just alternate inputs to the same one.

import pytest
from fastapi import HTTPException

import app.routers.daily_reports as dr_mod
from app.routers.daily_reports import (
    DailyReportCreate,
    health_check, get_reports, create_report, delete_all_reports,
    export_to_excel, export_to_pdf,
)


class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        if count is not None:
            self.count = count


class _FakeQuery:
    def __init__(self, table_name, state, response_map):
        self.table_name = table_name
        self.state = state
        self._response_map = response_map
        self._filters = []
        self._payload = None
        self._op = "select"
        self._count_mode = None

    def select(self, *a, count=None, **k):
        self._count_mode = count
        return self
    def eq(self, col, val):
        self._filters.append((col, val))
        return self
    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self
    def update(self, data):
        self._op = "update"
        self._payload = data
        return self
    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        table_cfg = self._response_map.get(self.table_name, {})
        if self._op == "insert":
            return _Resp(table_cfg.get("insert_return", [{"id": 1, **(self._payload or {})}]))
        if self._op == "update":
            return _Resp(table_cfg.get("update_return", [{"id": 1, **(self._payload or {})}]))
        if self._op == "delete":
            return _Resp(table_cfg.get("delete_return", []))
        select_returns = table_cfg.get("select_returns")
        if select_returns is not None:
            idx = self.state.setdefault("select_call_idx", {}).get(self.table_name, 0)
            self.state["select_call_idx"][self.table_name] = idx + 1
            return _Resp(select_returns[min(idx, len(select_returns) - 1)])
        if self._count_mode == "exact":
            return _Resp(table_cfg.get("select_return", []), count=table_cfg.get("count", 0))
        return _Resp(table_cfg.get("select_return", []))


class _FakeSupabase:
    def __init__(self, state, response_map):
        self.state = state
        self.response_map = response_map

    def table(self, name):
        return _FakeQuery(name, self.state, self.response_map)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(response_map: dict):
        state = {"calls": []}
        monkeypatch.setattr(dr_mod, "supabase", _FakeSupabase(state, response_map))
        return state
    return _patch


@pytest.fixture
def patch_supabase_none(monkeypatch):
    """Simulates the 'no DB configured' local-mode branch every handler carries."""
    monkeypatch.setattr(dr_mod, "supabase", None)


def _report_payload(**overrides):
    base = dict(date="2024-06-10")
    base.update(overrides)
    return DailyReportCreate(**base)


# ─── health_check ────────────────────────────────────────────────────────────────

async def test_health_check_local_mode_when_no_supabase(patch_supabase_none):
    result = await health_check()
    assert result["status"] == "connected"
    assert result["report_count"] == 0


async def test_health_check_healthy_reports_count(patch_supabase):
    patch_supabase({"daily_reports": {"select_return": [{"id": 1}], "count": 5}})
    result = await health_check()
    assert result["status"] == "healthy"
    assert result["report_count"] == 5


async def test_health_check_db_error_returns_error_status_not_a_crash(patch_supabase, monkeypatch):
    class _RaisingQuery:
        def select(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): raise Exception("connection refused")

    class _RaisingSupabase:
        def table(self, name): return _RaisingQuery()

    monkeypatch.setattr(dr_mod, "supabase", _RaisingSupabase())
    result = await health_check()
    assert result["status"] == "error"
    assert "connection refused" in result["message"]


# ─── get_reports ─────────────────────────────────────────────────────────────────

async def test_get_reports_local_mode_returns_sample_row(patch_supabase_none):
    result = await get_reports(start_date=None, end_date=None, limit=1000)
    assert len(result) == 1
    assert result[0]["id"] == 1


async def test_get_reports_decodes_json_fields(patch_supabase):
    records = [{"id": 2, "date": "2024-06-10", "call_outs": "[]", "equipment": "[]"}]
    patch_supabase({"daily_reports": {"select_return": records}})
    result = await get_reports(start_date=None, end_date=None, limit=1000)
    assert result[0]["call_outs"] == []
    assert result[0]["equipment"] == []


async def test_get_reports_raises_500_on_db_failure(patch_supabase, monkeypatch):
    class _RaisingQuery:
        def select(self, *a, **k): return self
        def gte(self, *a, **k): return self
        def lte(self, *a, **k): return self
        def order(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): raise Exception("db down")

    class _RaisingSupabase:
        def table(self, name): return _RaisingQuery()

    monkeypatch.setattr(dr_mod, "supabase", _RaisingSupabase())
    with pytest.raises(HTTPException) as exc_info:
        await get_reports(start_date=None, end_date=None, limit=1000)
    assert exc_info.value.status_code == 500


# ─── create_report ───────────────────────────────────────────────────────────────

async def test_create_report_local_mode_returns_dummy_row(patch_supabase_none):
    result = await create_report(_report_payload(), current_user={"user_id": "u1"})
    assert result["id"] == 1
    assert result["date"] == "2024-06-10"


async def test_create_report_inserts_new_when_no_existing_report_for_date(patch_supabase):
    state = patch_supabase({
        "daily_reports": {
            "select_return": [],
            "insert_return": [{"id": 3, "date": "2024-06-10", "call_outs": "[]", "equipment": "[]"}],
        },
    })
    result = await create_report(_report_payload(), current_user={"user_id": "u1"})
    assert result["id"] == 3
    assert [c for c in state["calls"] if c["op"] == "insert"]
    assert [c for c in state["calls"] if c["op"] == "update"] == []


async def test_create_report_updates_existing_report_for_same_date(patch_supabase):
    state = patch_supabase({
        "daily_reports": {
            "select_return": [{"id": 4, "date": "2024-06-10"}],
            "update_return": [{"id": 4, "date": "2024-06-10", "call_outs": "[]", "equipment": "[]"}],
        },
    })
    result = await create_report(_report_payload(safety="Updated safety note"), current_user={"user_id": "u1"})
    assert result["id"] == 4
    update_calls = [c for c in state["calls"] if c["op"] == "update"]
    assert len(update_calls) == 1
    assert [c for c in state["calls"] if c["op"] == "insert"] == []


async def test_create_report_raises_500_when_save_fails(patch_supabase):
    patch_supabase({"daily_reports": {"select_return": [], "insert_return": []}})
    with pytest.raises(HTTPException) as exc_info:
        await create_report(_report_payload(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


# ─── delete_all_reports ──────────────────────────────────────────────────────────

async def test_delete_all_reports_local_mode(patch_supabase_none):
    result = await delete_all_reports(current_user={"user_id": "u1", "role": "manager"})
    assert result == {"success": True, "detail": "No database connection", "deleted_count": 0}


async def test_delete_all_reports_nothing_to_delete(patch_supabase):
    patch_supabase({"daily_reports": {"select_return": []}})
    result = await delete_all_reports(current_user={"user_id": "u1", "role": "manager"})
    assert result == {"success": True, "detail": "No reports to delete", "deleted_count": 0}


async def test_delete_all_reports_deletes_and_reports_count(patch_supabase):
    state = patch_supabase({"daily_reports": {"select_return": [{"id": 1}, {"id": 2}, {"id": 3}]}})
    result = await delete_all_reports(current_user={"user_id": "u1", "role": "manager"})
    assert result == {"success": True, "detail": "All 3 reports deleted", "deleted_count": 3}
    assert any(c["op"] == "delete" for c in state["calls"])


# ─── export_to_excel / export_to_pdf (placeholders) ─────────────────────────────

async def test_export_to_excel_placeholder_echoes_date_range():
    result = await export_to_excel(start_date="2024-01-01", end_date="2024-01-31")
    assert result == {
        "message": "Excel export endpoint",
        "start_date": "2024-01-01",
        "end_date": "2024-01-31",
    }


async def test_export_to_pdf_placeholder_echoes_report_id():
    result = await export_to_pdf(42)
    assert result == {"message": "PDF export for report 42"}
