# tests/test_breakdowns_root_health.py — breakdowns_root() and health_check(), two
# endpoints with no prior coverage and no Supabase-query logic of their own
# (breakdowns.py was 61% covered; get_breakdowns/create/update/delete/dashboard/health
# had zero dedicated tests — test_breakdowns_heatmap.py and
# test_breakdowns_time_metrics.py only cover the heatmap endpoint and the pure calc
# helpers). Uses the sanctioned "call the route coroutine directly against a fake
# supabase client" recipe.

import pytest

import app.routers.breakdowns as bd
from app.routers.breakdowns import breakdowns_root, health_check


# ─── breakdowns_root ────────────────────────────────────────────────────────────────

async def test_root_lists_message_and_endpoints():
    result = await breakdowns_root()
    assert result["message"] == "Breakdowns Management API"
    assert result["status"] == "operational"
    assert result["endpoints"]["create_breakdown"] == "POST /"
    assert result["endpoints"]["delete"] == "DELETE /{id}"


# ─── health_check ───────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, should_raise=False):
        self._should_raise = should_raise

    def select(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        if self._should_raise:
            raise Exception("connection refused")
        return _Resp([{"id": "1"}])


class _FakeSupabase:
    def __init__(self, should_raise=False):
        self._should_raise = should_raise

    def table(self, _name):
        return _FakeQuery(self._should_raise)


async def test_health_check_healthy_when_query_succeeds(monkeypatch):
    monkeypatch.setattr(bd, "supabase", _FakeSupabase())
    result = await health_check()
    assert result["status"] == "healthy"
    assert result["database"] == "connected"
    assert "timestamp" in result


async def test_health_check_unhealthy_when_supabase_not_initialized(monkeypatch):
    monkeypatch.setattr(bd, "supabase", None)
    result = await health_check()
    assert result["status"] == "unhealthy"
    assert result["database"] == "not_connected"


async def test_health_check_unhealthy_when_query_raises(monkeypatch):
    monkeypatch.setattr(bd, "supabase", _FakeSupabase(should_raise=True))
    result = await health_check()
    assert result["status"] == "unhealthy"
    assert result["database"] == "connection_failed"
    assert "connection refused" in result["error"]
