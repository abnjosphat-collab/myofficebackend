# tests/test_ppe_matrix.py — the PPE matrix auto-recalculation: saving an interval
# (PUT /matrix) must recompute expiry for every existing active record of that type,
# not just future ones, and /matrix/apply-all must do the same across every type in
# one shot. Pure/mocked; no network.

import pytest

import app.routers.ppe as ppe_mod
from app.routers.ppe import (
    MatrixEntry, _apply_matrix_to_type, _add_months,
    set_ppe_matrix_entry, apply_ppe_matrix, apply_ppe_matrix_all,
)


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Chainable select/eq/update/upsert/execute — records what happened in `state`."""
    def __init__(self, state, table_name):
        self.state = state
        self.table_name = table_name
        self._mode = None
        self._filters = {}
        self._update_payload = None

    def select(self, *_a, **_k):
        self._mode = "select"
        return self

    def eq(self, field, value):
        self._filters[field] = value
        return self

    def update(self, payload):
        self._mode = "update"
        self._update_payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._mode = "upsert"
        self._update_payload = payload
        return self

    def execute(self):
        if self.table_name == "ppe_records":
            if self._mode == "select":
                rows = self.state["records"]
                ptype = self._filters.get("ppe_type")
                return _Result([r for r in rows if r["ppe_type"] == ptype and self._filters.get("status") == r.get("status", "active")])
            if self._mode == "update":
                self.state["updates"].append({"id": self._filters.get("id"), **self._update_payload})
                return _Result([{"id": self._filters.get("id")}])
        if self.table_name == "ppe_matrix":
            if self._mode == "select":
                return _Result(self.state["overrides"])
            if self._mode == "upsert":
                self.state["overrides"].append({"ppe_type": self._update_payload["ppe_type"], "interval_months": self._update_payload["interval_months"]})
                return _Result([self._update_payload])
        return _Result([])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(self.state, name)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {"records": [], "overrides": [], "updates": []}
    monkeypatch.setattr(ppe_mod, "supabase", _FakeSupabase(state))
    return state


def _manager():
    return {"user_id": "u-1", "email": "m@x.com", "role": "manager"}


# ─── _add_months (pure) ─────────────────────────────────────────────────────────

def test_add_months_clamps_day_overflow():
    assert _add_months("2026-01-31", 1) == "2026-02-28"


def test_add_months_zero_or_missing_returns_none():
    assert _add_months("2026-01-31", 0) is None
    assert _add_months(None, 6) is None


# ─── _apply_matrix_to_type ──────────────────────────────────────────────────────

def test_apply_matrix_recalculates_active_records(patch_supabase):
    patch_supabase["records"] = [
        {"id": 1, "ppe_type": "vest", "issue_date": "2026-01-15", "status": "active"},
        {"id": 2, "ppe_type": "vest", "issue_date": "2026-02-01", "status": "active"},
    ]
    updated = _apply_matrix_to_type("vest", 3)
    assert updated == 2
    assert patch_supabase["updates"][0] == {"id": 1, "expiry_date": "2026-04-15", "updated_at": patch_supabase["updates"][0]["updated_at"]}
    assert patch_supabase["updates"][1]["expiry_date"] == "2026-05-01"


def test_apply_matrix_interval_zero_clears_expiry(patch_supabase):
    patch_supabase["records"] = [{"id": 5, "ppe_type": "gloves", "issue_date": "2026-01-01", "status": "active"}]
    updated = _apply_matrix_to_type("gloves", 0)
    assert updated == 1
    assert patch_supabase["updates"][0]["expiry_date"] is None


def test_apply_matrix_no_active_records_is_a_noop(patch_supabase):
    patch_supabase["records"] = []
    assert _apply_matrix_to_type("helmet", 24) == 0
    assert patch_supabase["updates"] == []


# ─── set_ppe_matrix_entry (PUT /matrix) — must auto-recalculate on save ─────────

async def test_set_matrix_entry_saves_and_recalculates_existing_records(patch_supabase):
    patch_supabase["records"] = [
        {"id": 10, "ppe_type": "respirator", "issue_date": "2026-06-01", "status": "active"},
    ]
    result = await set_ppe_matrix_entry(MatrixEntry(ppe_type="respirator", interval_months=1), current_user=_manager())
    assert result == {"ppe_type": "respirator", "interval_months": 1, "updated": 1}
    assert patch_supabase["updates"][0]["expiry_date"] == "2026-07-01"
    # The interval itself was persisted too.
    assert {"ppe_type": "respirator", "interval_months": 1} in patch_supabase["overrides"]


async def test_set_matrix_entry_zero_records_updated_still_saves(patch_supabase):
    patch_supabase["records"] = []
    result = await set_ppe_matrix_entry(MatrixEntry(ppe_type="harness", interval_months=24), current_user=_manager())
    assert result == {"ppe_type": "harness", "interval_months": 24, "updated": 0}


# ─── apply_ppe_matrix (single type) ─────────────────────────────────────────────

async def test_apply_single_type_uses_override_over_default(patch_supabase):
    patch_supabase["overrides"] = [{"ppe_type": "vest", "interval_months": 3}]
    patch_supabase["records"] = [{"id": 20, "ppe_type": "vest", "issue_date": "2026-01-01", "status": "active"}]
    result = await apply_ppe_matrix("vest", current_user=_manager())
    assert result == {"ppe_type": "vest", "interval_months": 3, "updated": 1}


async def test_apply_single_type_unknown_type_rejected(patch_supabase):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await apply_ppe_matrix("not_a_real_type", current_user=_manager())
    assert exc.value.status_code == 400


# ─── apply_ppe_matrix_all ────────────────────────────────────────────────────────

async def test_apply_all_sums_updates_across_every_type(patch_supabase):
    patch_supabase["overrides"] = [{"ppe_type": "vest", "interval_months": 3}]
    patch_supabase["records"] = [
        {"id": 1, "ppe_type": "vest", "issue_date": "2026-01-01", "status": "active"},
        {"id": 2, "ppe_type": "helmet", "issue_date": "2026-01-01", "status": "active"},
        {"id": 3, "ppe_type": "gloves", "issue_date": "2026-01-01", "status": "active"},
    ]
    result = await apply_ppe_matrix_all(current_user=_manager())
    assert result["results"]["vest"] == 1
    assert result["results"]["helmet"] == 1
    assert result["results"]["gloves"] == 1
    assert result["total_updated"] == 3
