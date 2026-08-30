# tests/test_spares_suggestions_stats.py — get_suggestions, get_stats,
# spares_health_check, and export_spares had zero prior tests. get_stats in particular
# is real inventory-valuation logic (out_of_stock/low_stock/critical/safety_stock
# counts, total_value = sum(qty * price)) with branch conditions worth locking down
# exactly, not just "it returns something".
#
# NOTE — two pre-existing "never fake a 200 on failure" instances found while writing
# this file (see backend/docs/ENGINEERING_STANDARDS.md section 2):
#   - get_suggestions' except block returns {"suggestions": []} on ANY error. Left
#     as-is on purpose — this matches db_helpers.py's distinct_suggestions, which has
#     the SAME swallow-to-empty-list shape as a deliberate, documented design choice
#     ("a broken suggestions endpoint shouldn't break the form it's attached to").
#     Not the same class as a dashboard summary figure people make decisions from.
#   - get_stats' except block returned a fully zeroed stats dict on ANY error,
#     indistinguishable from "empty but healthy" inventory — a near-verbatim match to
#     the standards doc's own bad example. FIXED (2026-08-30) to re-raise instead.

import pytest
from fastapi import HTTPException

import app.routers.spares as spares_mod
from app.routers.spares import get_suggestions, get_stats, spares_health_check, export_spares


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return _Resp(self._data)


class _RaisingQuery:
    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        raise RuntimeError("simulated db failure")


class _FakeSupabase:
    def __init__(self, data=None, raise_error=False):
        self._data = data
        self._raise = raise_error

    def table(self, _name):
        return _RaisingQuery() if self._raise else _FakeQuery(self._data)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(data=None, raise_error=False):
        fake = _FakeSupabase(data=data, raise_error=raise_error)
        monkeypatch.setattr(spares_mod, "supabase", fake)
        return fake
    return _patch


# ─── get_suggestions ────────────────────────────────────────────────────────────────

async def test_get_suggestions_rejects_a_disallowed_field(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await get_suggestions("unit_price")
    assert exc_info.value.status_code == 400


async def test_get_suggestions_returns_sorted_unique_non_empty_values(patch_supabase):
    patch_supabase(data=[
        {"supplier": "Acme"}, {"supplier": "Zeta"}, {"supplier": "Acme"},
        {"supplier": ""}, {"supplier": None}, {},
    ])
    result = await get_suggestions("supplier")
    assert result == {"suggestions": ["Acme", "Zeta"]}


async def test_get_suggestions_no_rows_is_empty_list(patch_supabase):
    patch_supabase(data=[])
    result = await get_suggestions("category")
    assert result == {"suggestions": []}


async def test_get_suggestions_swallows_db_errors_to_an_empty_list(patch_supabase):
    # Documents the existing (flagged, not fixed) fake-200-on-failure behavior.
    patch_supabase(raise_error=True)
    result = await get_suggestions("category")
    assert result == {"suggestions": []}


# ─── get_stats ──────────────────────────────────────────────────────────────────────

async def test_get_stats_empty_inventory_is_all_zero(patch_supabase):
    patch_supabase(data=[])
    stats = await get_stats()
    assert stats == {
        "total": 0, "out_of_stock": 0, "low_stock": 0,
        "critical": 0, "safety_stock": 0, "total_value": 0,
    }


async def test_get_stats_classifies_out_of_stock_vs_low_stock_correctly(patch_supabase):
    patch_supabase(data=[
        {"current_quantity": 0, "min_quantity": 5, "unit_price": 10},   # out of stock (<=0)
        {"current_quantity": 3, "min_quantity": 5, "unit_price": 10},   # low stock (<=min, >0)
        {"current_quantity": 10, "min_quantity": 5, "unit_price": 10},  # neither
    ])
    stats = await get_stats()
    assert stats["total"] == 3
    assert stats["out_of_stock"] == 1
    assert stats["low_stock"] == 1


async def test_get_stats_counts_critical_priority_and_safety_stock_independently(patch_supabase):
    patch_supabase(data=[
        {"current_quantity": 10, "min_quantity": 1, "unit_price": 1, "priority": "critical", "safety_stock": True},
        {"current_quantity": 10, "min_quantity": 1, "unit_price": 1, "priority": "medium", "safety_stock": True},
        {"current_quantity": 10, "min_quantity": 1, "unit_price": 1, "priority": "critical", "safety_stock": False},
    ])
    stats = await get_stats()
    assert stats["critical"] == 2
    assert stats["safety_stock"] == 2


async def test_get_stats_total_value_is_quantity_times_price_summed_and_rounded(patch_supabase):
    patch_supabase(data=[
        {"current_quantity": 3, "min_quantity": 1, "unit_price": 3.333},   # 9.999
        {"current_quantity": 2, "min_quantity": 1, "unit_price": 1.111},   # 2.222
    ])
    stats = await get_stats()
    assert stats["total_value"] == 12.22  # 9.999 + 2.222 = 12.221 -> round(., 2)


async def test_get_stats_db_failure_raises_500_instead_of_faking_success(patch_supabase):
    # Was the exact anti-pattern named in ENGINEERING_STANDARDS.md #2's own example
    # (a fully zeroed "success" dict on any error) — fixed same day this test was
    # written (2026-08-30) to re-raise instead.
    patch_supabase(raise_error=True)
    with pytest.raises(HTTPException) as exc_info:
        await get_stats()
    assert exc_info.value.status_code == 500


# ─── spares_health_check ────────────────────────────────────────────────────────────

async def test_health_check_healthy_path(patch_supabase):
    patch_supabase(data=[{"id": 1}])
    result = await spares_health_check()
    assert result["status"] == "healthy"
    assert result["service"] == "spares"
    assert result["database"] == "connected"
    assert result["table_exists"] is True


async def test_health_check_reports_unhealthy_on_db_error(patch_supabase):
    patch_supabase(raise_error=True)
    result = await spares_health_check()
    assert result["status"] == "unhealthy"
    assert "error" in result


# ─── export_spares ──────────────────────────────────────────────────────────────────

async def test_export_spares_happy_path_shape(patch_supabase):
    patch_supabase(data=[{"id": 1, "stock_code": "SC-1"}, {"id": 2, "stock_code": "SC-2"}])
    result = await export_spares()
    assert result["count"] == 2
    assert len(result["spares"]) == 2
    assert "export_date" in result


async def test_export_spares_db_error_raises_500(patch_supabase):
    patch_supabase(raise_error=True)
    with pytest.raises(HTTPException) as exc_info:
        await export_spares()
    assert exc_info.value.status_code == 500
