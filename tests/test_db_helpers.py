# tests/test_db_helpers.py — direct unit tests for the shared Supabase query helpers
# used across 17+ routers (get_or_404, apply_date_range, or_ilike,
# distinct_suggestions, status_choice_validator). These had no DIRECT tests of their
# own — only incidental coverage from whatever router tests happened to exercise them
# indirectly — despite a regression here having the widest possible blast radius in
# the backend. Explicit, isolated tests lock in the actual contract.

import pytest
from fastapi import HTTPException

from app.db_helpers import (
    get_or_404, apply_date_range, or_ilike, distinct_suggestions, status_choice_validator,
)


# ─── get_or_404 ──────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, response_data):
        self._response = response_data
        self._eq_calls = []

    def select(self, *a, **k): return self
    def eq(self, col, val):
        self._eq_calls.append((col, val))
        return self
    def execute(self): return _Resp(self._response)


class _FakeDB:
    def __init__(self, response_data):
        self._table = _FakeTable(response_data)

    def table(self, _name):
        return self._table


def test_get_or_404_returns_the_first_row():
    db = _FakeDB([{"id": 1, "name": "Widget"}])
    assert get_or_404(db, "spares", 1) == {"id": 1, "name": "Widget"}


def test_get_or_404_raises_404_when_no_row():
    db = _FakeDB([])
    with pytest.raises(HTTPException) as exc_info:
        get_or_404(db, "spares", 999)
    assert exc_info.value.status_code == 404


def test_get_or_404_uses_a_custom_id_column():
    db = _FakeDB([{"employee_id": "E001"}])
    get_or_404(db, "employees", "E001", id_col="employee_id")
    assert db._table._eq_calls == [("employee_id", "E001")]


def test_get_or_404_uses_the_custom_detail_message():
    db = _FakeDB([])
    with pytest.raises(HTTPException) as exc_info:
        get_or_404(db, "spares", 1, detail="Spare part not found")
    assert exc_info.value.detail == "Spare part not found"


# ─── apply_date_range ────────────────────────────────────────────────────────────────

class _FakeQuery:
    def __init__(self):
        self.calls = []

    def gte(self, col, val):
        self.calls.append(("gte", col, val))
        return self

    def lte(self, col, val):
        self.calls.append(("lte", col, val))
        return self


def test_apply_date_range_with_both_bounds():
    q = _FakeQuery()
    apply_date_range(q, "date", "2024-01-01", "2024-01-31")
    assert q.calls == [("gte", "date", "2024-01-01"), ("lte", "date", "2024-01-31")]


def test_apply_date_range_with_neither_bound_is_a_noop():
    q = _FakeQuery()
    result = apply_date_range(q, "date", None, None)
    assert q.calls == []
    assert result is q


def test_apply_date_range_with_only_start():
    q = _FakeQuery()
    apply_date_range(q, "date", "2024-01-01", None)
    assert q.calls == [("gte", "date", "2024-01-01")]


# ─── or_ilike ────────────────────────────────────────────────────────────────────────

def test_or_ilike_builds_the_postgrest_or_expression():
    result = or_ilike(["name", "description"], "pump")
    assert result == "name.ilike.%pump%,description.ilike.%pump%"


def test_or_ilike_single_column():
    assert or_ilike(["name"], "x") == "name.ilike.%x%"


# ─── distinct_suggestions ────────────────────────────────────────────────────────────

class _SuggestResp:
    def __init__(self, data):
        self.data = data


class _SuggestTable:
    def __init__(self, response_data, raise_error=False):
        self._response = response_data
        self._raise = raise_error

    def select(self, *a, **k): return self
    def neq(self, *a, **k): return self

    @property
    def not_(self):
        # Real postgrest usage is `.not_.is_(...)` — not_ is accessed as a plain
        # attribute (no call), which then exposes the negated filter methods.
        return self

    def is_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def ilike(self, *a, **k): return self

    def execute(self):
        if self._raise:
            raise Exception("simulated failure")
        return _SuggestResp(self._response)


class _SuggestDB:
    def __init__(self, response_data, raise_error=False):
        self._table = _SuggestTable(response_data, raise_error)

    def table(self, _name):
        return self._table


async def test_distinct_suggestions_returns_sorted_unique_values():
    db = _SuggestDB([{"department": "Mechanical"}, {"department": "Electrical"}, {"department": "Mechanical"}])
    result = await distinct_suggestions(db, "reports", "department", None, "department")
    assert result == ["Electrical", "Mechanical"]


async def test_distinct_suggestions_ignores_blank_values():
    db = _SuggestDB([{"department": "Mechanical"}, {"department": ""}, {"department": None}])
    result = await distinct_suggestions(db, "reports", "department", None, "department")
    assert result == ["Mechanical"]


async def test_distinct_suggestions_swallows_errors_to_empty_list(caplog):
    # A broken suggestions endpoint shouldn't break the form it's attached to.
    db = _SuggestDB([], raise_error=True)
    result = await distinct_suggestions(db, "reports", "department", None, "department")
    assert result == []


# ─── status_choice_validator ─────────────────────────────────────────────────────────

def test_status_choice_validator_accepts_an_allowed_value():
    validate = status_choice_validator(["Pending", "Completed"], "bad status")
    assert validate(None, "Pending") == "Pending"


def test_status_choice_validator_rejects_a_disallowed_value():
    validate = status_choice_validator(["Pending", "Completed"], "bad status")
    with pytest.raises(ValueError, match="bad status"):
        validate(None, "Unknown")
