# tests/test_compressors_misc.py — the small standalone endpoints/helpers left
# over after the CRUD/daily-entry/readings/stats/analytics/export groups:
# root(), health_check(), test_endpoint(), get_supabase(), and the on_startup()
# hook. All zero coverage before this file.

import app.routers.compressors as compressors_mod
from app.routers.compressors import root, health_check, test_endpoint, get_supabase, on_startup


async def test_root_lists_the_advertised_endpoints():
    result = await root()
    assert result["status"] == "online"
    assert result["version"] == "2.0.0"
    assert any("compressors" in e for e in result["endpoints"])


async def test_health_check_reports_healthy_with_a_timestamp():
    result = await health_check()
    assert result["status"] == "healthy"
    assert "timestamp" in result


async def test_test_endpoint_smoke():
    result = await test_endpoint()
    assert result["message"] == "Compressors API is working"
    assert "timestamp" in result


def test_get_supabase_returns_the_shared_client():
    from app.supabase_client import supabase as shared_client
    assert get_supabase() is shared_client


async def test_on_startup_does_not_raise():
    await on_startup()


async def test_on_startup_swallows_a_failure_from_get_supabase(monkeypatch):
    def _boom():
        raise Exception("no credentials configured")
    monkeypatch.setattr(compressors_mod, "get_supabase", _boom)
    await on_startup()  # must not raise -- startup failures are logged, not fatal
