# tests/test_employees_search.py — search_employees routes to a different Supabase
# filter depending on search_by (name/id/id_number/email/all), with zero tests
# confirming each branch actually queries the field it claims to. Also applies
# _dates_from_db to every result, not just returning raw rows.

from datetime import date

import pytest

from fastapi import HTTPException

import app.routers.employees as emp_mod
from app.routers.employees import search_employees
from app.routers.equipment import generate_equipment_id


def test_generate_equipment_id_has_the_expected_prefix():
    assert generate_equipment_id().startswith("EQ-")


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, state, response_data):
        self.state = state
        self._response = response_data

    def select(self, *a, **k): return self

    def or_(self, expr):
        self.state["method"] = "or_"
        self.state["expr"] = expr
        return self

    def ilike(self, column, value):
        self.state["method"] = "ilike"
        self.state["column"] = column
        self.state["value"] = value
        return self

    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, state, response_data):
        self.state = state
        self._response = response_data

    def table(self, _name):
        return _FakeQuery(self.state, self._response)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(response_data=None):
        state = {}
        monkeypatch.setattr(emp_mod, "supabase", _FakeSupabase(state, response_data or []))
        return state
    return _patch


async def test_search_by_id_uses_ilike_on_employee_id(patch_supabase):
    state = patch_supabase()
    await search_employees("E001", search_by="id")
    assert state["method"] == "ilike"
    assert state["column"] == "employee_id"


async def test_search_by_email_uses_ilike_on_email(patch_supabase):
    state = patch_supabase()
    await search_employees("john@x.com", search_by="email")
    assert state["column"] == "email"


async def test_search_by_id_number_uses_ilike_on_id_number(patch_supabase):
    state = patch_supabase()
    await search_employees("ID123", search_by="id_number")
    assert state["method"] == "ilike"
    assert state["column"] == "id_number"


async def test_search_db_failure_is_500(monkeypatch):
    class _BoomQuery:
        def select(self, *a, **k): return self
        def or_(self, *a, **k): return self
        def execute(self): raise RuntimeError("db down")

    class _BoomSupabase:
        def table(self, _name): return _BoomQuery()

    monkeypatch.setattr(emp_mod, "supabase", _BoomSupabase())
    with pytest.raises(HTTPException) as exc:
        await search_employees("John", search_by="name")
    assert exc.value.status_code == 500


async def test_search_by_name_uses_or_across_first_and_last_name(patch_supabase):
    state = patch_supabase()
    await search_employees("John", search_by="name")
    assert state["method"] == "or_"
    assert "first_name" in state["expr"]
    assert "last_name" in state["expr"]


async def test_search_all_covers_every_field(patch_supabase):
    state = patch_supabase()
    await search_employees("John", search_by="all")
    assert "first_name" in state["expr"]
    assert "employee_id" in state["expr"]
    assert "id_number" in state["expr"]


async def test_results_have_dates_converted(patch_supabase):
    patch_supabase([{"date_of_engagement": "2020-03-15"}])
    result = await search_employees("John", search_by="name")
    assert result[0]["date_of_engagement"] == date(2020, 3, 15)


async def test_no_matches_is_an_empty_list(patch_supabase):
    patch_supabase([])
    result = await search_employees("Nobody", search_by="name")
    assert result == []
