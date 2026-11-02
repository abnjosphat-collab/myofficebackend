# tests/test_ppe_matrix_scoped.py — department-scoped Mining matrix (SOLUTION batch 2).
#
# The global matrix used to recalculate every record across all sections on any
# save; a Mining save would rewrite Engineering rows. These tests pin the scoped
# contract: company behaviour unchanged, Mining rows save scoped, recalculation
# touches only that department's records, unknown policy stays unknown.
import app.routers.ppe as ppe_mod
from app.routers.ppe import (
    _apply_matrix_to_type,
    _effective_matrix,
    apply_ppe_matrix,
    get_ppe_matrix,
    set_ppe_matrix_entry,
)


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
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
        self.state["upserts"].append({"payload": payload, "on_conflict": on_conflict})
        if self.table_name == "ppe_matrix":
            key = (payload["ppe_type"], payload.get("department", ""))
            self.state["matrix"] = [r for r in self.state["matrix"]
                                    if (r["ppe_type"], r.get("department", "")) != key]
            self.state["matrix"].append(dict(payload))
        return self

    def execute(self):
        if self.table_name == "ppe_records" and self._mode == "select":
            rows = self.state["records"]
            out = [r for r in rows if r["ppe_type"] == self._filters.get("ppe_type")
                   and r.get("status", "active") == self._filters.get("status")]
            if "department" in self._filters:
                out = [r for r in out if r.get("department") == self._filters["department"]]
            return _Result(out)
        if self.table_name == "ppe_records" and self._mode == "update":
            self.state["updates"].append({"id": self._filters.get("id"), **self._update_payload})
            return _Result([{"id": self._filters.get("id")}])
        if self.table_name == "ppe_matrix" and self._mode == "select":
            rows = self.state["matrix"]
            if "department" in self._filters:
                rows = [r for r in rows if r.get("department", "") == self._filters["department"]]
            return _Result(rows)
        return _Result([])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(self.state, name)


import pytest


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {"records": [], "matrix": [], "updates": [], "upserts": []}
    monkeypatch.setattr(ppe_mod, "supabase", _FakeSupabase(state))
    return state


def _manager():
    return {"user_id": "u-1", "email": "m@x.com", "role": "manager"}


def _mining_viewer():
    return {"user_id": "u-9", "email": "mine@x.com", "role": "viewer",
            "scope": {"kind": "department", "value": "Mining"}}


def _entry(ppe_type, months, department=None):
    return type("E", (), {"ppe_type": ppe_type, "interval_months": months, "department": department})()


async def test_effective_matrix_layers_company_then_department(patch_supabase):
    patch_supabase["matrix"] = [
        {"ppe_type": "gumboots", "interval_months": 6, "department": ""},
        {"ppe_type": "gumboots", "interval_months": 3, "department": "Mining"},
        {"ppe_type": "jacket", "interval_months": 24, "department": "Mining"},
    ]
    company = _effective_matrix(None)
    assert company["gumboots"] == 6
    mining = _effective_matrix("Mining")
    assert mining["gumboots"] == 3
    assert mining["jacket"] == 24


async def test_scoped_recalc_touches_only_that_department(patch_supabase):
    patch_supabase["records"] = [
        {"id": 1, "ppe_type": "gumboots", "issue_date": "2026-01-15", "status": "active", "department": "Mining"},
        {"id": 2, "ppe_type": "gumboots", "issue_date": "2026-01-15", "status": "active", "department": "Engineering"},
    ]
    updated = _apply_matrix_to_type("gumboots", 3, "Mining")
    assert updated == 1
    assert [u["id"] for u in patch_supabase["updates"]] == [1]


async def test_put_scoped_row_saves_with_department(patch_supabase):
    patch_supabase["records"] = [
        {"id": 1, "ppe_type": "gumboots", "issue_date": "2026-01-15", "status": "active", "department": "Mining"}
    ]
    result = await set_ppe_matrix_entry(_entry("gumboots", 3, "Mining"), _manager())
    assert result["department"] == "Mining"
    assert result["updated"] == 1
    saved = [{k: r.get(k) for k in ("ppe_type", "interval_months", "department")} for r in patch_supabase["matrix"]]
    assert {"ppe_type": "gumboots", "interval_months": 3, "department": "Mining"} in saved


async def test_put_rejects_unknown_scope(patch_supabase):
    with pytest.raises(Exception, match="company"):
        await set_ppe_matrix_entry(_entry("gumboots", 3, "Atlantis"), _manager())


async def test_company_save_keeps_company_scope_and_recalculates_everywhere(patch_supabase):
    patch_supabase["records"] = [
        {"id": 1, "ppe_type": "vest", "issue_date": "2026-01-15", "status": "active", "department": "Mining"},
        {"id": 2, "ppe_type": "vest", "issue_date": "2026-01-15", "status": "active", "department": "Engineering"},
    ]
    result = await set_ppe_matrix_entry(_entry("vest", 3, None), _manager())
    assert result["department"] is None
    assert result["updated"] == 2


async def test_mining_account_gets_mining_matrix_by_default(patch_supabase):
    patch_supabase["matrix"] = [
        {"ppe_type": "gumboots", "interval_months": 6, "department": ""},
        {"ppe_type": "gumboots", "interval_months": 3, "department": "Mining"},
    ]
    matrix = await get_ppe_matrix(department=None, current_user=_mining_viewer())
    assert matrix["gumboots"] == 3


async def test_company_account_get_is_unchanged(patch_supabase):
    patch_supabase["matrix"] = [
        {"ppe_type": "gumboots", "interval_months": 6, "department": ""},
        {"ppe_type": "gumboots", "interval_months": 3, "department": "Mining"},
    ]
    matrix = await get_ppe_matrix(department=None, current_user=_manager())
    assert matrix["gumboots"] == 6


async def test_apply_type_honours_department(patch_supabase):
    patch_supabase["matrix"] = [
        {"ppe_type": "gumboots", "interval_months": 6, "department": ""},
        {"ppe_type": "gumboots", "interval_months": 3, "department": "Mining"}
    ]
    patch_supabase["records"] = [
        {"id": 1, "ppe_type": "gumboots", "issue_date": "2026-01-15", "status": "active", "department": "Mining"},
        {"id": 2, "ppe_type": "gumboots", "issue_date": "2026-01-15", "status": "active", "department": "Engineering"}
    ]
    result = await apply_ppe_matrix("gumboots", department="Mining", current_user=_manager())
    assert result["interval_months"] == 3
    assert [u["id"] for u in patch_supabase["updates"]] == [1]
