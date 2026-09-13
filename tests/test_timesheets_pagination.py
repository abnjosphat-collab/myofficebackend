# Regression: get_timesheets must paginate past PostgREST's 1000-row cap.

import pytest

import app.routers.timesheets as ts_mod
from app.routers.timesheets import get_timesheets


class _Resp:
    def __init__(self, data):
        self.data = data


class _PaginatedQuery:
    def __init__(self, all_rows):
        self._all = list(all_rows)
        self._range = None
        self._filters = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def gte(self, *a, **k):
        return self

    def lte(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        assert self._range is not None
        s, e = self._range
        return _Resp(self._all[s : e + 1])


class _FakeSupabase:
    def __init__(self, rows):
        self._rows = rows
        self.ranges = []

    def table(self, name):
        assert name == "timesheets"
        q = _PaginatedQuery(self._rows)
        orig_range = q.range

        def tracked_range(start, end):
            self.ranges.append((start, end))
            return orig_range(start, end)

        q.range = tracked_range  # type: ignore[method-assign]
        return q


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(rows):
        fake = _FakeSupabase(rows)
        monkeypatch.setattr(ts_mod, "supabase", fake)
        return fake
    return _patch


async def test_get_timesheets_paginates_past_1000_row_cap(patch_supabase):
    page1 = [{"id": i, "date": "2026-08-20", "employee_id": i % 40} for i in range(1000)]
    page2 = [{"id": 1000 + i, "date": "2026-08-13", "employee_id": 100 + i} for i in range(50)]
    all_rows = page1 + page2
    fake = patch_supabase(all_rows)

    result = await get_timesheets(
        employee_id=None,
        start_date=__import__("datetime").date(2026, 8, 13),
        end_date=__import__("datetime").date(2026, 9, 12),
    )

    assert len(result) == 1050
    assert fake.ranges == [(0, 999), (1000, 1999)]


async def test_get_timesheets_stops_after_short_page(patch_supabase):
    rows = [{"id": i, "date": "2026-08-13", "employee_id": i} for i in range(10)]
    fake = patch_supabase(rows)
    result = await get_timesheets(employee_id=None, start_date=None, end_date=None)
    assert len(result) == 10
    assert fake.ranges == [(0, 999)]
