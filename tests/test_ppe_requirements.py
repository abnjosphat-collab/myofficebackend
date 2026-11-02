# tests/test_ppe_requirements.py — Batch 4: PPE size requirements register.
#
# Requirements are planned allocations, never issues: the endpoint lists them
# with fulfilment derived from active issue records, and importing
# requirements must never fabricate ppe_records or issue dates.
import pytest

import app.routers.ppe as ppe_mod
from app.routers.ppe import list_ppe_requirements


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, state, table_name):
        self.state = state
        self.table_name = table_name
        self._filters = {}
        self._in_filters = {}

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        self._filters[field] = value
        return self

    def or_(self, _expression):
        # Mirrors exclude_separate_departments: Mining rows drop out of
        # unscoped reads; blank departments stay.
        self._filters["__not_separate__"] = True
        return self

    def in_(self, field, values):
        self._in_filters[field] = list(values)
        return self

    def range(self, *_a):
        return self

    def execute(self):
        rows = self.state.get(self.table_name, [])
        out = [r for r in rows
               if all(r.get(k) == v for k, v in self._filters.items() if k != "__not_separate__")
               and all(r.get(k) in v for k, v in self._in_filters.items())]
        if self._filters.get("__not_separate__"):
            out = [r for r in out if (r.get("department") or "") != "Mining"]
        return _Result(out)


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(self.state, name)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {"ppe_requirements": [], "ppe_records": []}
    monkeypatch.setattr(ppe_mod, "supabase", _FakeSupabase(state))
    return state


def _manager():
    return {"user_id": "u-1", "email": "m@x.com", "role": "manager"}


def _requirement(name, number=None, size='L', department='Engineering'):
    return {"id": f"req-{name}", "employee_id": number, "source_name": name,
            "employee_name": name, "department": department, "mine_section": "Mechanical",
            "ppe_type": "rainsuit", "size_source": size, "size_canonical": size,
            "quantity": 1, "status": "required", "source_ref": "raincoat-2026-10", "notes": ""}


async def test_fulfilment_derives_from_active_rainsuit_records(patch_supabase):
    patch_supabase["ppe_requirements"] = [
        _requirement("David Muronda", "C1001"),
        _requirement("T. Kapenzi", "C1002", size="S"),
        _requirement("Togara", None),
    ]
    patch_supabase["ppe_records"] = [
        {"id": 1, "employee_id": "C1001", "ppe_type": "rainsuit", "status": "active"},
        {"id": 2, "employee_id": "C1002", "ppe_type": "helmet", "status": "active"},
        {"id": 3, "employee_id": "C1002", "ppe_type": "rainsuit", "status": "returned"},
    ]
    found = await list_ppe_requirements(department="Engineering", current_user=_manager())
    by_name = {r["source_name"]: r for r in found}
    assert by_name["David Muronda"]["fulfilled"] is True
    assert by_name["T. Kapenzi"]["fulfilled"] is False  # helmet is not a rainsuit; returned is not active
    assert by_name["Togara"]["fulfilled"] is False  # unmatched stays visible, never fulfilled


async def test_department_filter_scopes_register(patch_supabase):
    patch_supabase["ppe_requirements"] = [
        _requirement("David Muronda", "C1001", department="Engineering"),
        _requirement("Tendai Moyo", "M100", department="Mining"),
    ]
    found = await list_ppe_requirements(department="Engineering", current_user=_manager())
    assert [r["source_name"] for r in found] == ["David Muronda"]
