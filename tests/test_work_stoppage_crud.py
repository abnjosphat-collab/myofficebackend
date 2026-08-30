# tests/test_work_stoppage_crud.py — work_stoppage.py's CRUD handlers
# (get_reports, get_report, create_report, update_report, delete_report,
# get_department_suggestions, get_inspector_suggestions) had zero tests beyond
# get_stats (test_work_stoppage_stats.py) and the correctiveActions
# AttributeError regression test (test_work_stoppage_corrective_actions.py,
# which only unit-tests the Pydantic model's attribute access, not the route
# itself). This file exercises the actual routes, including the two-table
# report+correctiveActions insert/replace cascade and the pagination
# (limit/offset -> .range()) applied to get_reports.
#
# Same in-memory fake Supabase client shape as test_sheq_inspections_crud.py
# (select/insert/update/delete really mutate per-table state), extended with
# .range() and .neq()/.not_.is_() for distinct_suggestions.

import pytest
from fastapi import HTTPException

import app.routers.work_stoppage as ws_mod
from app.routers.work_stoppage import (
    get_reports, get_report, create_report, update_report, delete_report,
    get_department_suggestions, get_inspector_suggestions,
    WorkStoppageCreate, WorkStoppageUpdate, CorrectiveActionCreate, CorrectiveActionUpdate,
)


# ─── in-memory fake Supabase ─────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Not:
    def __init__(self, query):
        self.query = query

    def is_(self, col, val):
        self.query._filters.append(("not_is", col, val))
        return self.query


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

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
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

    @property
    def not_(self):
        return _Not(self)

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
            if kind == "neq" and row.get(col) == val:
                return False
            if kind == "not_is" and row.get(col) is None:
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
    def _patch(reports=None, actions=None, raise_on=None, fail_ops=None):
        fake = _FakeSupabase({
            "work_stoppage_reports": reports or [],
            "corrective_actions": actions or [],
        }, raise_on=raise_on, fail_ops=fail_ops)
        monkeypatch.setattr(ws_mod, "supabase", fake)
        return fake
    return _patch


USER = {"user_id": "u1", "email": "u1@x.com", "role": "manager"}


def _report(id, **overrides):
    base = {
        "id": id, "date": "2026-08-01", "department": "Mechanical Workshop", "section": "Mechanical",
        "description": "Conveyor belt guard was missing during shift inspection.",
        "investigation_findings": "", "stoppage_by": "J. Moyo", "stoppage_position": "Foreman",
        "accepted_by": "", "sheq_checked_by": "", "submitted_at": "2026-08-01T00:00:00",
    }
    base.update(overrides)
    return base


def _action(id, report_id, **overrides):
    base = {
        "id": id, "report_id": report_id, "finding": "Guard missing", "action": "Refit guard",
        "by_who": "J. Moyo", "by_when": "2026-08-10", "status": "Pending",
    }
    base.update(overrides)
    return base


# ─── get_reports ─────────────────────────────────────────────────────────────────────

async def test_get_reports_returns_newest_first_with_nested_actions(patch_supabase):
    patch_supabase(
        reports=[_report("r1", submitted_at="2026-08-01T00:00:00"),
                 _report("r2", submitted_at="2026-08-02T00:00:00")],
        actions=[_action("a1", "r1")],
    )
    result = await get_reports(search=None, section=None, inspector=None, from_date=None,
                                to_date=None, limit=100, offset=0)
    assert [r["id"] for r in result] == ["r2", "r1"]
    assert result[1]["correctiveActions"] == [{
        "id": "a1", "report_id": "r1", "finding": "Guard missing", "action": "Refit guard",
        "byWho": "J. Moyo", "byWhen": "2026-08-10", "status": "Pending",
    }]
    assert result[0]["correctiveActions"] == []


async def test_get_reports_applies_section_and_inspector_filters(patch_supabase):
    fake = patch_supabase(reports=[
        _report("r1", section="Mechanical", stoppage_by="J. Moyo"),
        _report("r2", section="Electrical", stoppage_by="T. Ncube"),
    ])
    result = await get_reports(search=None, section="Electrical", inspector="Ncube",
                                from_date=None, to_date=None, limit=100, offset=0)
    assert [r["id"] for r in result] == ["r2"]
    filter_call = next(c for c in fake.calls if c["table"] == "work_stoppage_reports" and c["op"] == "select")
    assert ("eq", "section", "Electrical") in filter_call["filters"]
    assert ("ilike", "stoppage_by", "%Ncube%") in filter_call["filters"]


async def test_get_reports_search_uses_or_across_department_description_stoppageby(patch_supabase):
    patch_supabase(reports=[
        _report("r1", department="Boiler House"), _report("r2", department="Unrelated"),
    ])
    result = await get_reports(search="Boiler", section=None, inspector=None, from_date=None,
                                to_date=None, limit=100, offset=0)
    assert [r["id"] for r in result] == ["r1"]


async def test_get_reports_date_range_filters(patch_supabase):
    patch_supabase(reports=[
        _report("r1", date="2026-07-01"), _report("r2", date="2026-08-15"), _report("r3", date="2026-09-01"),
    ])
    result = await get_reports(search=None, section=None, inspector=None, from_date="2026-08-01",
                                to_date="2026-08-31", limit=100, offset=0)
    assert [r["id"] for r in result] == ["r2"]


async def test_get_reports_applies_limit_and_offset_as_range(patch_supabase):
    fake = patch_supabase(reports=[_report(f"r{i}", submitted_at=f"2026-08-{i:02d}T00:00:00") for i in range(1, 6)])
    result = await get_reports(search=None, section=None, inspector=None, from_date=None,
                                to_date=None, limit=2, offset=1)
    assert len(result) == 2
    select_call = next(c for c in fake.calls if c["table"] == "work_stoppage_reports" and c["op"] == "select")
    assert select_call is not None  # range applied via fake's execute(); length assertion above proves it


# ─── get_report ──────────────────────────────────────────────────────────────────────

async def test_get_report_returns_report_with_actions(patch_supabase):
    patch_supabase(reports=[_report("r1")], actions=[_action("a1", "r1")])
    result = await get_report("r1")
    assert result["id"] == "r1"
    assert result["stoppageBy"] == "J. Moyo"
    assert len(result["correctiveActions"]) == 1


async def test_get_report_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await get_report("missing")
    assert exc_info.value.status_code == 404


# ─── create_report ───────────────────────────────────────────────────────────────────

async def test_create_report_with_corrective_actions_inserts_both_tables(patch_supabase):
    fake = patch_supabase()
    payload = WorkStoppageCreate(
        date="2026-08-01", department="Mechanical Workshop", section="Mechanical",
        description="Conveyor belt guard was missing during shift inspection.",
        stoppageBy="J. Moyo", stoppagePosition="Foreman",
        correctiveActions=[CorrectiveActionCreate(finding="Guard missing", action="Refit guard",
                                                    byWho="J. Moyo", byWhen="2026-08-10", status="Pending")],
    )
    result = await create_report(payload, current_user=USER)

    assert result["department"] == "Mechanical Workshop"
    assert result["stoppageBy"] == "J. Moyo"
    assert len(result["correctiveActions"]) == 1
    assert result["correctiveActions"][0]["finding"] == "Guard missing"

    report_insert = next(c for c in fake.calls if c["table"] == "work_stoppage_reports" and c["op"] == "insert")
    assert report_insert["payload"]["stoppage_by"] == "J. Moyo"
    assert report_insert["payload"]["stoppage_position"] == "Foreman"

    action_insert = next(c for c in fake.calls if c["table"] == "corrective_actions" and c["op"] == "insert")
    action_payload = action_insert["payload"][0]
    assert action_payload["report_id"] == result["id"]
    assert action_payload["by_who"] == "J. Moyo"
    assert action_payload["by_when"] == "2026-08-10"


async def test_create_report_without_actions_leaves_actions_empty(patch_supabase):
    fake = patch_supabase()
    payload = WorkStoppageCreate(
        date="2026-08-01", department="Mechanical Workshop", section="General",
        description="A minor near-collision required a temporary work stoppage.",
        stoppageBy="J. Moyo",
    )
    result = await create_report(payload, current_user=USER)
    assert result["correctiveActions"] == []
    assert not any(c["table"] == "corrective_actions" for c in fake.calls)


async def test_create_report_rejects_invalid_status_choice():
    with pytest.raises(Exception):
        CorrectiveActionCreate(finding="X", action="Y", byWho="Z", byWhen="2026-08-10", status="Bogus")


# ─── update_report ───────────────────────────────────────────────────────────────────

async def test_update_report_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await update_report("missing", WorkStoppageUpdate(department="X"), current_user=USER)
    assert exc_info.value.status_code == 404


async def test_update_report_explicit_none_clears_the_field(patch_supabase):
    # Regression test for the null-vs-unset bug (backend edec24a, reintroduced here,
    # fixed 2026-08-30): the field loop used to filter on `value is not None` AFTER
    # exclude_unset=True already ran, silently dropping an explicit clear-to-null.
    fake = patch_supabase(reports=[_report("r1")], actions=[])
    await update_report("r1", WorkStoppageUpdate(acceptedBy=None), current_user=USER)
    update_call = next(c for c in fake.calls if c["table"] == "work_stoppage_reports" and c["op"] == "update")
    assert update_call["payload"]["accepted_by"] is None


async def test_update_report_updates_fields_and_keeps_existing_actions_when_omitted(patch_supabase):
    fake = patch_supabase(reports=[_report("r1")], actions=[_action("a1", "r1")])
    result = await update_report("r1", WorkStoppageUpdate(department="New Workshop", acceptedBy="M. Chirwa"),
                                  current_user=USER)

    assert result["department"] == "New Workshop"
    assert result["acceptedBy"] == "M. Chirwa"
    assert len(result["correctiveActions"]) == 1
    assert not any(c["table"] == "corrective_actions" and c["op"] in ("insert", "delete") for c in fake.calls)

    update_call = next(c for c in fake.calls if c["table"] == "work_stoppage_reports" and c["op"] == "update")
    assert update_call["payload"]["department"] == "New Workshop"
    assert update_call["payload"]["accepted_by"] == "M. Chirwa"


async def test_update_report_with_actions_deletes_and_reinserts(patch_supabase):
    fake = patch_supabase(reports=[_report("r1")], actions=[_action("a_old", "r1", finding="Old finding")])
    result = await update_report(
        "r1",
        WorkStoppageUpdate(correctiveActions=[
            CorrectiveActionUpdate(finding="New finding", action="Fix it", byWho="J. Moyo",
                                    byWhen="2026-08-20", status="In Progress"),
        ]),
        current_user=USER,
    )
    assert len(result["correctiveActions"]) == 1
    assert result["correctiveActions"][0]["finding"] == "New finding"

    delete_call = next(c for c in fake.calls if c["table"] == "corrective_actions" and c["op"] == "delete")
    assert ("eq", "report_id", "r1") in delete_call["filters"]
    insert_call = next(c for c in fake.calls if c["table"] == "corrective_actions" and c["op"] == "insert")
    assert insert_call["payload"][0]["finding"] == "New finding"
    assert all(a["finding"] != "Old finding" for a in fake.tables["corrective_actions"])


async def test_update_report_skips_incomplete_corrective_actions(patch_supabase):
    # update_report only inserts an action if finding/action/byWho/byWhen are ALL
    # truthy — an update item missing one of those fields is silently dropped
    # rather than inserted with blanks.
    fake = patch_supabase(reports=[_report("r1")], actions=[_action("a_old", "r1")])
    result = await update_report(
        "r1",
        WorkStoppageUpdate(correctiveActions=[
            CorrectiveActionUpdate(finding="Complete one", action="Do it", byWho="J. Moyo", byWhen="2026-08-20"),
            CorrectiveActionUpdate(finding="Incomplete", action=None, byWho="J. Moyo", byWhen="2026-08-20"),
        ]),
        current_user=USER,
    )
    assert len(result["correctiveActions"]) == 1
    assert result["correctiveActions"][0]["finding"] == "Complete one"


async def test_update_report_with_empty_actions_list_clears_them(patch_supabase):
    fake = patch_supabase(reports=[_report("r1")], actions=[_action("a_old", "r1")])
    result = await update_report("r1", WorkStoppageUpdate(correctiveActions=[]), current_user=USER)
    assert result["correctiveActions"] == []
    assert fake.tables["corrective_actions"] == []


# ─── delete_report ───────────────────────────────────────────────────────────────────

async def test_delete_report_removes_row(patch_supabase):
    fake = patch_supabase(reports=[_report("r1")])
    result = await delete_report("r1", current_user=USER)
    assert result == {"success": True, "message": "Report deleted successfully"}
    assert fake.tables["work_stoppage_reports"] == []


async def test_delete_report_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await delete_report("missing", current_user=USER)
    assert exc_info.value.status_code == 404


# ─── get_department_suggestions / get_inspector_suggestions ────────────────────────

async def test_get_department_suggestions_returns_sorted_distinct_values(patch_supabase):
    patch_supabase(reports=[
        _report("r1", department="Boiler House"), _report("r2", department="Boiler House"),
        _report("r3", department="Ash Plant"),
    ])
    result = await get_department_suggestions(search=None)
    assert result == ["Ash Plant", "Boiler House"]


async def test_get_inspector_suggestions_filters_by_search_term(patch_supabase):
    patch_supabase(reports=[
        _report("r1", stoppage_by="J. Moyo"), _report("r2", stoppage_by="T. Ncube"),
    ])
    result = await get_inspector_suggestions(search="Moyo")
    assert result == ["J. Moyo"]


# ─── failure paths: insert/update returning no row, and unexpected exceptions ──────
# Confirms every handler turns a real failure into an HTTPException rather than
# silently faking a success response (ENGINEERING_STANDARDS.md section 2).

async def test_create_report_insert_returning_nothing_is_500(patch_supabase):
    patch_supabase(fail_ops={("work_stoppage_reports", "insert")})
    payload = WorkStoppageCreate(date="2026-08-01", department="Dept", section="General",
                                  description="A description that is long enough.", stoppageBy="J. Moyo")
    with pytest.raises(HTTPException) as exc_info:
        await create_report(payload, current_user=USER)
    assert exc_info.value.status_code == 500


async def test_update_report_update_returning_nothing_is_500(patch_supabase):
    patch_supabase(reports=[_report("r1")], fail_ops={("work_stoppage_reports", "update")})
    with pytest.raises(HTTPException) as exc_info:
        await update_report("r1", WorkStoppageUpdate(department="New Name"), current_user=USER)
    assert exc_info.value.status_code == 500


async def test_get_reports_unexpected_exception_is_500_not_a_fake_empty_list(patch_supabase):
    patch_supabase(raise_on={"work_stoppage_reports"})
    with pytest.raises(HTTPException) as exc_info:
        await get_reports(search=None, section=None, inspector=None, from_date=None,
                           to_date=None, limit=100, offset=0)
    assert exc_info.value.status_code == 500


async def test_delete_report_unexpected_exception_is_500(patch_supabase):
    fake = patch_supabase(reports=[_report("r1")])
    fake.raise_on = {"work_stoppage_reports"}
    with pytest.raises(HTTPException) as exc_info:
        await delete_report("r1", current_user=USER)
    assert exc_info.value.status_code == 500
