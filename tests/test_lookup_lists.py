# tests/test_lookup_lists.py — add_lookup_value's idempotent case-insensitive dedup
# (a match already on the list is returned as-is, never creating a duplicate entry) had
# zero tests. Generic backing store for every "pick from a growing list, or type a new
# value and have it remembered" field in the app.

import pytest

import app.routers.lookup_lists as ll_mod
from app.routers.lookup_lists import LookupValueCreate, add_lookup_value, rename_lookup_value, delete_lookup_value


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, state, existing_match):
        self.state = state
        self._existing_match = existing_match
        self._op = "select"
        self._payload = None

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def ilike(self, *a, **k): return self
    def order(self, *a, **k): return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        self.state.setdefault("calls", []).append(self._op)
        if self._op == "select":
            return _Resp([self._existing_match] if self._existing_match else [])
        if self._op == "insert":
            return _Resp([{"id": "new", **self._payload}])
        if self._op == "update":
            return _Resp([{"id": "1", **self._payload}] if self.state.get("row_exists", True) else [])
        if self._op == "delete":
            return _Resp([{"id": "1"}] if self.state.get("row_exists", True) else [])


class _FakeSupabase:
    def __init__(self, state, existing_match=None):
        self.state = state
        self.existing_match = existing_match

    def table(self, _name):
        return _FakeQuery(self.state, self.existing_match)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(existing_match=None, row_exists=True):
        state = {"row_exists": row_exists}
        monkeypatch.setattr(ll_mod, "supabase", _FakeSupabase(state, existing_match))
        return state
    return _patch


async def test_add_returns_existing_case_insensitive_match_without_inserting(patch_supabase):
    state = patch_supabase(existing_match={"id": "1", "value": "Bearing Failure"})
    result = await add_lookup_value("breakdown_nature", LookupValueCreate(value="bearing failure"))
    assert result == {"id": "1", "value": "Bearing Failure"}
    assert "insert" not in state["calls"]


async def test_add_inserts_a_genuinely_new_value(patch_supabase):
    state = patch_supabase(existing_match=None)
    result = await add_lookup_value("breakdown_nature", LookupValueCreate(value="New Nature"))
    assert result["value"] == "New Nature"
    assert "insert" in state["calls"]


async def test_add_strips_whitespace_before_checking_and_inserting(patch_supabase):
    state = patch_supabase(existing_match=None)
    await add_lookup_value("location", LookupValueCreate(value="  Loading Bay  "))
    assert state["calls"].count("insert") == 1


async def test_rename_not_found_is_404(patch_supabase):
    patch_supabase(row_exists=False)
    with pytest.raises(Exception) as exc_info:
        await rename_lookup_value("location", 999, LookupValueCreate(value="New Name"))
    assert getattr(exc_info.value, "status_code", None) == 404


async def test_rename_success_returns_updated_row(patch_supabase):
    patch_supabase(row_exists=True)
    result = await rename_lookup_value("location", 1, LookupValueCreate(value="Corrected Name"))
    assert result["value"] == "Corrected Name"


async def test_delete_not_found_is_404(patch_supabase):
    patch_supabase(row_exists=False)
    with pytest.raises(Exception) as exc_info:
        await delete_lookup_value("location", 999)
    assert getattr(exc_info.value, "status_code", None) == 404


async def test_delete_success(patch_supabase):
    patch_supabase(row_exists=True)
    result = await delete_lookup_value("location", 1)
    assert result == {"success": True}
