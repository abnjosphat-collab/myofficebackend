# tests/test_near_miss_crud.py — near_miss.py's CRUD handlers (get_reports,
# get_report, create_report, update_report, delete_report) had zero tests
# beyond get_stats (test_near_miss_stats.py). Unlike sheq_inspections.py and
# work_stoppage.py, near_miss reports have no nested child table — this file
# still uses the same in-memory fake Supabase shape (for consistency with the
# other two SHEQ router test files and to exercise real filter/range
# behavior), just against a single table.
#
# near_miss.py accesses `response.data` / `response.data[0]` directly rather
# than through the rows()/one_row() helpers the other two files use — noted
# here since it means an empty `.data` list and a falsy value both take the
# same "not found" branch, which the tests below confirm is still correct.

import pytest
from fastapi import HTTPException

import app.routers.near_miss as nm_mod
from app.routers.near_miss import (
    get_reports, get_report, create_report, update_report, delete_report,
    NearMissCreate, NearMissUpdate,
)
from app.routers.near_miss import test_endpoint as _router_test_endpoint


# ─── in-memory fake Supabase (single table, but same shape as the sibling files) ────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self._filters = []
        self._or_expr = None
        self._op = "select"
        self._payload = None
        self._order = None
        self._range = None

    def select(self, *a, **k):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def ilike(self, col, pattern):
        self._filters.append(("ilike", col, pattern))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def lte(self, col, val):
        self._filters.append(("lte", col, val))
        return self

    def or_(self, expr):
        self._or_expr = expr
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def _match(self, row):
        for kind, col, val in self._filters:
            if kind == "eq" and row.get(col) != val:
                return False
            if kind == "ilike":
                term = val.strip("%").lower()
                if term not in str(row.get(col, "")).lower():
                    return False
            if kind == "gte" and not (row.get(col) is not None and row.get(col) >= val):
                return False
            if kind == "lte" and not (row.get(col) is not None and row.get(col) <= val):
                return False
        if self._or_expr:
            terms = []
            for part in self._or_expr.split(","):
                col, _, rest = part.partition(".ilike.")
                terms.append((col, rest.strip("%").lower()))
            if not any(t in str(row.get(col, "")).lower() for col, t in terms):
                return False
        return True

    def execute(self):
        self.db.calls.append({
            "table": self.table_name, "op": self._op,
            "filters": list(self._filters), "or": self._or_expr, "payload": self._payload,
        })
        rows_list = self.db.tables.setdefault(self.table_name, [])
        if (self.table_name, self._op) in self.db.fail_ops:
            return _Resp([])
        if self._op == "select":
            result = [r for r in rows_list if self._match(r)]
            if self._order:
                col, desc = self._order
                result = sorted(result, key=lambda r: r.get(col), reverse=desc)
            if self._range:
                start, end = self._range
                result = result[start:end + 1]
            return _Resp([dict(r) for r in result])
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in payload:
                rows_list.append(dict(p))
            return _Resp([dict(p) for p in payload])
        if self._op == "update":
            matched = [r for r in rows_list if self._match(r)]
            for r in matched:
                r.update(self._payload)
            return _Resp([dict(r) for r in matched])
        if self._op == "delete":
            matched = [r for r in rows_list if self._match(r)]
            for r in matched:
                rows_list.remove(r)
            return _Resp([dict(r) for r in matched])
        return _Resp([])


class _FakeSupabase:
    def __init__(self, tables=None, raise_on=None, fail_ops=None):
        self.tables = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.calls = []
        self.raise_on = raise_on or set()
        self.fail_ops = fail_ops or set()

    def table(self, name):
        if name in self.raise_on:
            raise RuntimeError("simulated database outage")
        return _Query(self, name)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(reports=None, raise_on=None, fail_ops=None):
        fake = _FakeSupabase({"nearmiss_reports": reports or []}, raise_on=raise_on, fail_ops=fail_ops)
        monkeypatch.setattr(nm_mod, "supabase", fake)
        return fake
    return _patch


USER = {"user_id": "u1", "email": "u1@x.com", "role": "manager"}


def _report(id, **overrides):
    base = {
        "id": id, "department": "Mechanical Workshop", "section": "Mechanical",
        "date": "2026-08-01", "time": "08:00", "location": "Bay 3",
        "description": "A forklift nearly struck a technician near the loading bay.",
        "witnessdetails": "", "reportername": "A. Ncube", "submitted_at": "2026-08-01T00:00:00",
    }
    base.update(overrides)
    return base


# ─── get_reports ─────────────────────────────────────────────────────────────────────

async def test_get_reports_returns_newest_first_mapped_to_camel(patch_supabase):
    patch_supabase(reports=[
        _report("r1", submitted_at="2026-08-01T00:00:00"),
        _report("r2", submitted_at="2026-08-02T00:00:00"),
    ])
    result = await get_reports(search=None, section=None, reporter=None, from_date=None,
                                to_date=None, limit=100, offset=0)
    assert [r["id"] for r in result] == ["r2", "r1"]
    assert result[0]["reporterName"] == "A. Ncube"
    assert result[0]["submittedAt"] == "2026-08-02T00:00:00"


async def test_get_reports_applies_section_and_reporter_filters(patch_supabase):
    fake = patch_supabase(reports=[
        _report("r1", section="Mechanical", reportername="A. Ncube"),
        _report("r2", section="Electrical", reportername="B. Sibanda"),
    ])
    result = await get_reports(search=None, section="Electrical", reporter="Sibanda",
                                from_date=None, to_date=None, limit=100, offset=0)
    assert [r["id"] for r in result] == ["r2"]
    filter_call = next(c for c in fake.calls if c["table"] == "nearmiss_reports" and c["op"] == "select")
    assert ("eq", "section", "Electrical") in filter_call["filters"]
    assert ("ilike", "reportername", "%Sibanda%") in filter_call["filters"]


async def test_get_reports_search_uses_or_across_department_location_description(patch_supabase):
    patch_supabase(reports=[_report("r1", location="Bay 3"), _report("r2", location="Unrelated")])
    result = await get_reports(search="Bay 3", section=None, reporter=None, from_date=None,
                                to_date=None, limit=100, offset=0)
    assert [r["id"] for r in result] == ["r1"]


async def test_get_reports_date_range_filters(patch_supabase):
    patch_supabase(reports=[
        _report("r1", date="2026-07-01"), _report("r2", date="2026-08-15"), _report("r3", date="2026-09-01"),
    ])
    result = await get_reports(search=None, section=None, reporter=None, from_date="2026-08-01",
                                to_date="2026-08-31", limit=100, offset=0)
    assert [r["id"] for r in result] == ["r2"]


async def test_get_reports_applies_limit_and_offset(patch_supabase):
    patch_supabase(reports=[_report(f"r{i}", submitted_at=f"2026-08-{i:02d}T00:00:00") for i in range(1, 6)])
    result = await get_reports(search=None, section=None, reporter=None, from_date=None,
                                to_date=None, limit=2, offset=1)
    assert len(result) == 2


async def test_get_reports_no_matches_is_empty_list(patch_supabase):
    patch_supabase(reports=[])
    result = await get_reports(search=None, section=None, reporter=None, from_date=None,
                                to_date=None, limit=100, offset=0)
    assert result == []


# ─── get_report ──────────────────────────────────────────────────────────────────────

async def test_get_report_returns_mapped_report(patch_supabase):
    patch_supabase(reports=[_report("r1")])
    result = await get_report("r1")
    assert result["id"] == "r1"
    assert result["reporterName"] == "A. Ncube"
    assert result["location"] == "Bay 3"


async def test_get_report_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await get_report("missing")
    assert exc_info.value.status_code == 404


# ─── create_report ───────────────────────────────────────────────────────────────────

async def test_create_report_inserts_lowercase_fields_and_returns_camel(patch_supabase):
    fake = patch_supabase()
    payload = NearMissCreate(
        department="Mechanical Workshop", section="Mechanical", date="2026-08-01", time="08:00",
        location="Bay 3", description="A forklift nearly struck a technician near the loading bay.",
        witnessDetails="Two colleagues present.", reporterName="A. Ncube",
    )
    result = await create_report(payload, current_user=USER)

    assert result["department"] == "Mechanical Workshop"
    assert result["reporterName"] == "A. Ncube"
    assert result["witnessDetails"] == "Two colleagues present."
    assert "submittedAt" in result

    insert_call = next(c for c in fake.calls if c["table"] == "nearmiss_reports" and c["op"] == "insert")
    assert insert_call["payload"]["witnessdetails"] == "Two colleagues present."
    assert insert_call["payload"]["reportername"] == "A. Ncube"


async def test_create_report_defaults_optional_fields_to_empty_string(patch_supabase):
    fake = patch_supabase()
    payload = NearMissCreate(
        department="Mechanical Workshop", section="General", date="2026-08-01", time="08:00",
        location="Bay 3", description="A forklift nearly struck a technician near the loading bay.",
    )
    result = await create_report(payload, current_user=USER)
    assert result["witnessDetails"] == ""
    assert result["reporterName"] == ""


# ─── update_report ───────────────────────────────────────────────────────────────────

async def test_update_report_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await update_report("missing", NearMissUpdate(department="X"), current_user=USER)
    assert exc_info.value.status_code == 404


async def test_update_report_updates_provided_fields(patch_supabase):
    fake = patch_supabase(reports=[_report("r1")])
    result = await update_report("r1", NearMissUpdate(location="Bay 7", reporterName="B. Sibanda"),
                                  current_user=USER)
    assert result["location"] == "Bay 7"
    assert result["reporterName"] == "B. Sibanda"
    assert result["department"] == "Mechanical Workshop"  # untouched field preserved

    update_call = next(c for c in fake.calls if c["table"] == "nearmiss_reports" and c["op"] == "update")
    assert update_call["payload"] == {"location": "Bay 7", "reportername": "B. Sibanda"}


async def test_update_report_with_no_fields_set_returns_existing_without_writing(patch_supabase):
    fake = patch_supabase(reports=[_report("r1")])
    result = await update_report("r1", NearMissUpdate(), current_user=USER)
    assert result["id"] == "r1"
    assert not any(c["table"] == "nearmiss_reports" and c["op"] == "update" for c in fake.calls)


# ─── delete_report ───────────────────────────────────────────────────────────────────

async def test_delete_report_removes_row(patch_supabase):
    fake = patch_supabase(reports=[_report("r1")])
    result = await delete_report("r1", current_user=USER)
    assert result == {"success": True, "message": "Report deleted successfully"}
    assert fake.tables["nearmiss_reports"] == []


async def test_delete_report_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await delete_report("missing", current_user=USER)
    assert exc_info.value.status_code == 404


# ─── failure paths: insert/update returning no row, and unexpected exceptions ──────
# Confirms every handler turns a real failure into an HTTPException rather than
# silently faking a success response (ENGINEERING_STANDARDS.md section 2).

async def test_create_report_insert_returning_nothing_is_500(patch_supabase):
    patch_supabase(fail_ops={("nearmiss_reports", "insert")})
    payload = NearMissCreate(department="Dept", section="General", date="2026-08-01", time="08:00",
                              location="Bay 3", description="A description that is long enough.")
    with pytest.raises(HTTPException) as exc_info:
        await create_report(payload, current_user=USER)
    assert exc_info.value.status_code == 500


async def test_update_report_update_returning_nothing_is_500(patch_supabase):
    patch_supabase(reports=[_report("r1")], fail_ops={("nearmiss_reports", "update")})
    with pytest.raises(HTTPException) as exc_info:
        await update_report("r1", NearMissUpdate(location="New Bay"), current_user=USER)
    assert exc_info.value.status_code == 500


async def test_get_reports_unexpected_exception_is_500_not_a_fake_empty_list(patch_supabase):
    patch_supabase(raise_on={"nearmiss_reports"})
    with pytest.raises(HTTPException) as exc_info:
        await get_reports(search=None, section=None, reporter=None, from_date=None,
                           to_date=None, limit=100, offset=0)
    assert exc_info.value.status_code == 500


async def test_delete_report_unexpected_exception_is_500(patch_supabase):
    fake = patch_supabase(reports=[_report("r1")])
    fake.raise_on = {"nearmiss_reports"}
    with pytest.raises(HTTPException) as exc_info:
        await delete_report("r1", current_user=USER)
    assert exc_info.value.status_code == 500


# ─── /test — router liveness check ──────────────────────────────────────────────────

async def test_test_endpoint_reports_success():
    result = await _router_test_endpoint()
    assert result["status"] == "success"
    assert "timestamp" in result
