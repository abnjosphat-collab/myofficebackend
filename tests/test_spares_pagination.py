# tests/test_spares_pagination.py — get_spares' pagination loop works around
# Supabase PostgREST's hard 1000-row-per-request cap by looping with .range() until a
# page comes back short. This exact bug class ("a list endpoint silently truncated at
# ~1000 rows") already bit overtime.py once (see crud_router.py's own comment) — zero
# tests confirm spares.py's version actually retrieves more than one page. The fake
# here actually respects .range(start, end) slicing (not just returning everything
# regardless of range), so the multi-page loop is genuinely exercised, not just
# trivially satisfied.

import pytest

import app.routers.spares as spares_mod
from app.routers.spares import get_spares


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, all_rows):
        self._all_rows = all_rows
        self._start = 0
        self._end = None

    def select(self, *a, **k): return self
    def or_(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self

    def range(self, start, end):
        self._start = start
        self._end = end
        return self

    def execute(self):
        # Slice is inclusive of `end`, matching Supabase's .range() semantics.
        return _Resp(self._all_rows[self._start:self._end + 1])


class _FakeSupabase:
    def __init__(self, all_rows):
        self._all_rows = all_rows

    def table(self, _name):
        return _FakeQuery(self._all_rows)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(all_rows):
        monkeypatch.setattr(spares_mod, "supabase", _FakeSupabase(all_rows))
    return _patch


async def test_fewer_than_one_page_returns_everything_in_a_single_fetch(patch_supabase):
    rows = [{"id": i, "stock_code": f"SC-{i}"} for i in range(50)]
    patch_supabase(rows)
    result = await get_spares(limit=100_000, offset=0)
    assert len(result) == 50


async def test_retrieves_more_than_the_1000_row_postgrest_cap(patch_supabase):
    # 1500 rows spans exactly 2 pages (1000 + 500) — proves the loop doesn't stop
    # after the first page the way the pre-fix bug did.
    rows = [{"id": i, "stock_code": f"SC-{i:05d}"} for i in range(1500)]
    patch_supabase(rows)
    result = await get_spares(limit=100_000, offset=0)
    assert len(result) == 1500
    assert result[0]["id"] == 0
    assert result[-1]["id"] == 1499


async def test_missing_categories_defaults_to_empty_list(patch_supabase):
    patch_supabase([{"id": 1, "stock_code": "SC-1"}])
    result = await get_spares(limit=100_000, offset=0)
    assert result[0]["categories"] == []


async def test_existing_categories_are_preserved(patch_supabase):
    patch_supabase([{"id": 1, "stock_code": "SC-1", "categories": ["Bearings"]}])
    result = await get_spares(limit=100_000, offset=0)
    assert result[0]["categories"] == ["Bearings"]


async def test_empty_result_set(patch_supabase):
    patch_supabase([])
    result = await get_spares(limit=100_000, offset=0)
    assert result == []
