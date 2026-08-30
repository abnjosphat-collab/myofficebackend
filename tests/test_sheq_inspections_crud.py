# tests/test_sheq_inspections_crud.py — sheq_inspections.py's CRUD handlers
# (get_inspections, get_inspection, create_inspection, update_inspection,
# delete_inspection, get_findings, add_finding, update_finding, delete_finding)
# had zero tests beyond get_inspection_stats (see test_sheq_inspection_stats.py),
# despite the parent+child (inspection + findings) insert/replace logic being the
# riskiest part of this file: create_inspection inserts into two tables, and
# update_inspection deletes-then-reinserts every finding when `findings` is
# provided but leaves them untouched (re-fetching instead) when it's omitted.
#
# Uses a small in-memory fake Supabase client (select/insert/update/delete all
# actually mutate a per-table list, filtered by .eq/.ilike/.or_/.gte/.lte), so
# the two-table cascade is exercised for real rather than asserted via a canned
# return value that could mask the handler doing the wrong thing.

import pytest
from fastapi import HTTPException

import app.routers.sheq_inspections as sheq_mod
from app.routers.sheq_inspections import (
    get_inspections, get_inspection, create_inspection, update_inspection,
    delete_inspection, get_findings, add_finding, update_finding, delete_finding,
    SHEQCreate, SHEQUpdate, FindingCreate, FindingUpdate,
)


# ─── in-memory fake Supabase — select/insert/update/delete really mutate state ──────

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
    def _patch(inspections=None, findings=None, raise_on=None, fail_ops=None):
        fake = _FakeSupabase({
            "sheq_inspections": inspections or [],
            "sheq_findings": findings or [],
        }, raise_on=raise_on, fail_ops=fail_ops)
        monkeypatch.setattr(sheq_mod, "supabase", fake)
        return fake
    return _patch


USER = {"user_id": "u1", "email": "u1@x.com", "role": "manager"}


def _insp(id, **overrides):
    base = {
        "id": id, "inspectors": "John Doe", "title": "Monthly Check", "place": "Plant A",
        "date": "2026-08-01", "time": "08:00", "department": "Ops", "section": "mechanical",
        "hodname": "H. Odhiambo", "sheqofficialname": "S. Official", "hodsignature": None,
        "sheqsignature": None, "status": "draft", "before_photos": [], "after_photos": [],
        "created_at": "2026-08-01T00:00:00", "updated_at": "2026-08-01T00:00:00",
    }
    base.update(overrides)
    return base


def _finding(id, inspection_id, **overrides):
    base = {
        "id": id, "inspection_id": inspection_id, "finding": "Guard missing",
        "requiredaction": "Refit guard", "bywho": "J. Moyo", "bywhen": "2026-08-10",
        "status": "open", "priority": "high", "section": "mechanical",
        "completeddate": None, "remarks": None,
    }
    base.update(overrides)
    return base


# ─── get_inspections ─────────────────────────────────────────────────────────────────

async def test_get_inspections_returns_newest_first_with_nested_findings(patch_supabase):
    patch_supabase(
        inspections=[_insp("i1", created_at="2026-08-01T00:00:00"),
                     _insp("i2", created_at="2026-08-02T00:00:00")],
        findings=[_finding("f1", "i1")],
    )
    result = await get_inspections(search=None, section=None, status=None, inspector=None,
                                    from_date=None, to_date=None)
    assert [r["id"] for r in result] == ["i2", "i1"]
    assert result[1]["findings"] == [{
        "id": "f1", "finding": "Guard missing", "requiredAction": "Refit guard",
        "byWho": "J. Moyo", "byWhen": "2026-08-10", "status": "open", "priority": "high",
        "section": "mechanical", "completedDate": None, "remarks": None,
    }]
    assert result[0]["findings"] == []


async def test_get_inspections_applies_section_status_and_inspector_filters(patch_supabase):
    fake = patch_supabase(inspections=[
        _insp("i1", section="mechanical", status="draft", inspectors="John Doe"),
        _insp("i2", section="electrical", status="closed", inspectors="Jane Smith"),
    ])
    result = await get_inspections(search=None, section="electrical", status="closed",
                                    inspector="Jane", from_date=None, to_date=None)
    assert [r["id"] for r in result] == ["i2"]
    filter_call = next(c for c in fake.calls if c["table"] == "sheq_inspections" and c["op"] == "select")
    assert ("eq", "section", "electrical") in filter_call["filters"]
    assert ("eq", "status", "closed") in filter_call["filters"]
    assert ("ilike", "inspectors", "%Jane%") in filter_call["filters"]


async def test_get_inspections_search_uses_or_across_title_inspectors_place(patch_supabase):
    patch_supabase(inspections=[
        _insp("i1", title="Boiler Inspection"), _insp("i2", title="Unrelated"),
    ])
    result = await get_inspections(search="Boiler", section=None, status=None, inspector=None,
                                    from_date=None, to_date=None)
    assert [r["id"] for r in result] == ["i1"]


async def test_get_inspections_date_range_applies_gte_and_lte(patch_supabase):
    patch_supabase(inspections=[
        _insp("i1", date="2026-07-01"), _insp("i2", date="2026-08-15"), _insp("i3", date="2026-09-01"),
    ])
    result = await get_inspections(search=None, section=None, status=None, inspector=None,
                                    from_date="2026-08-01", to_date="2026-08-31")
    assert [r["id"] for r in result] == ["i2"]


# ─── get_inspection ──────────────────────────────────────────────────────────────────

async def test_get_inspection_returns_inspection_with_findings(patch_supabase):
    patch_supabase(inspections=[_insp("i1")], findings=[_finding("f1", "i1")])
    result = await get_inspection("i1")
    assert result["id"] == "i1"
    assert result["hodName"] == "H. Odhiambo"
    assert len(result["findings"]) == 1
    assert result["findings"][0]["id"] == "f1"


async def test_get_inspection_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await get_inspection("missing")
    assert exc_info.value.status_code == 404


# ─── create_inspection ───────────────────────────────────────────────────────────────

async def test_create_inspection_with_findings_inserts_both_tables(patch_supabase):
    fake = patch_supabase()
    payload = SHEQCreate(
        inspectors="John Doe", title="Monthly Check", place="Plant A", date="2026-08-01",
        time="08:00", department="Ops", section="mechanical", hodName="H. Odhiambo",
        sheqOfficialName="S. Official", status="draft",
        findings=[FindingCreate(finding="Guard missing", requiredAction="Refit guard",
                                 byWho="J. Moyo", byWhen="2026-08-10", status="open",
                                 priority="high", section="mechanical")],
    )
    result = await create_inspection(payload, current_user=USER)

    assert result["title"] == "Monthly Check"
    assert result["hodName"] == "H. Odhiambo"
    assert len(result["findings"]) == 1
    assert result["findings"][0]["finding"] == "Guard missing"
    assert result["findings"][0]["byWho"] == "J. Moyo"

    insp_insert = next(c for c in fake.calls if c["table"] == "sheq_inspections" and c["op"] == "insert")
    assert insp_insert["payload"]["hodname"] == "H. Odhiambo"
    assert insp_insert["payload"]["sheqofficialname"] == "S. Official"

    finding_insert = next(c for c in fake.calls if c["table"] == "sheq_findings" and c["op"] == "insert")
    finding_payload = finding_insert["payload"][0]
    assert finding_payload["inspection_id"] == result["id"]
    assert finding_payload["requiredaction"] == "Refit guard"
    assert finding_payload["bywho"] == "J. Moyo"
    assert finding_payload["bywhen"] == "2026-08-10"


async def test_create_inspection_without_findings_leaves_findings_empty(patch_supabase):
    fake = patch_supabase()
    payload = SHEQCreate(
        inspectors="John Doe", title="Quick Check", place="Plant A", date="2026-08-01",
        time="08:00", section="mechanical",
    )
    result = await create_inspection(payload, current_user=USER)
    assert result["findings"] == []
    assert not any(c["table"] == "sheq_findings" for c in fake.calls)


# ─── update_inspection ───────────────────────────────────────────────────────────────

async def test_update_inspection_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await update_inspection("missing", SHEQUpdate(title="X"), current_user=USER)
    assert exc_info.value.status_code == 404


async def test_update_inspection_updates_fields_and_keeps_existing_findings_when_omitted(patch_supabase):
    fake = patch_supabase(inspections=[_insp("i1")], findings=[_finding("f1", "i1")])
    result = await update_inspection("i1", SHEQUpdate(title="Renamed", hodName="New HOD"), current_user=USER)

    assert result["title"] == "Renamed"
    assert result["hodName"] == "New HOD"
    # findings untouched (re-fetched, not deleted/reinserted)
    assert len(result["findings"]) == 1
    assert result["findings"][0]["id"] == "f1"
    assert not any(c["table"] == "sheq_findings" and c["op"] in ("insert", "delete") for c in fake.calls)

    update_call = next(c for c in fake.calls if c["table"] == "sheq_inspections" and c["op"] == "update")
    assert update_call["payload"]["title"] == "Renamed"
    assert update_call["payload"]["hodname"] == "New HOD"
    assert "updated_at" in update_call["payload"]


async def test_update_inspection_with_findings_deletes_and_reinserts(patch_supabase):
    fake = patch_supabase(inspections=[_insp("i1")], findings=[_finding("f_old", "i1", finding="Old finding")])
    result = await update_inspection(
        "i1",
        SHEQUpdate(findings=[FindingUpdate(finding="New finding", requiredAction="Fix it",
                                            byWho="J. Moyo", byWhen="2026-08-20", status="open",
                                            priority="low", section="mechanical")]),
        current_user=USER,
    )
    assert len(result["findings"]) == 1
    assert result["findings"][0]["finding"] == "New finding"

    delete_call = next(c for c in fake.calls if c["table"] == "sheq_findings" and c["op"] == "delete")
    assert ("eq", "inspection_id", "i1") in delete_call["filters"]
    insert_call = next(c for c in fake.calls if c["table"] == "sheq_findings" and c["op"] == "insert")
    assert insert_call["payload"][0]["finding"] == "New finding"
    # old finding is really gone from the fake table, not just absent from the response
    assert all(f["finding"] != "Old finding" for f in fake.tables["sheq_findings"])


async def test_update_inspection_with_empty_findings_list_clears_them(patch_supabase):
    fake = patch_supabase(inspections=[_insp("i1")], findings=[_finding("f_old", "i1")])
    result = await update_inspection("i1", SHEQUpdate(findings=[]), current_user=USER)
    assert result["findings"] == []
    assert fake.tables["sheq_findings"] == []
    assert not any(c["table"] == "sheq_findings" and c["op"] == "insert" for c in fake.calls)


async def test_update_inspection_empty_photo_list_clears_photos(patch_supabase):
    fake = patch_supabase(inspections=[_insp("i1", before_photos=["a.jpg"])])
    result = await update_inspection("i1", SHEQUpdate(before_photos=[]), current_user=USER)
    assert result["before_photos"] == []
    update_call = next(c for c in fake.calls if c["table"] == "sheq_inspections" and c["op"] == "update")
    assert update_call["payload"]["before_photos"] == []


# ─── delete_inspection ───────────────────────────────────────────────────────────────

async def test_delete_inspection_removes_row(patch_supabase):
    fake = patch_supabase(inspections=[_insp("i1")])
    result = await delete_inspection("i1", current_user=USER)
    assert result == {"success": True, "message": "Inspection deleted successfully"}
    assert fake.tables["sheq_inspections"] == []


async def test_delete_inspection_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await delete_inspection("missing", current_user=USER)
    assert exc_info.value.status_code == 404


# ─── get_findings ────────────────────────────────────────────────────────────────────

async def test_get_findings_returns_camel_cased_findings_for_inspection(patch_supabase):
    patch_supabase(findings=[_finding("f1", "i1"), _finding("f2", "i2")])
    result = await get_findings("i1")
    assert len(result) == 1
    assert result[0]["id"] == "f1"
    assert result[0]["requiredAction"] == "Refit guard"


async def test_get_findings_no_matches_is_empty_list(patch_supabase):
    patch_supabase()
    result = await get_findings("i1")
    assert result == []


# ─── add_finding ─────────────────────────────────────────────────────────────────────

async def test_add_finding_inserts_and_returns_camel_finding(patch_supabase):
    fake = patch_supabase(inspections=[_insp("i1")])
    result = await add_finding(
        "i1",
        FindingCreate(finding="Loose bolt", requiredAction="Tighten", byWho="A. Banda",
                      byWhen="2026-08-12", status="open", priority="medium", section="mechanical"),
        current_user=USER,
    )
    assert result["finding"] == "Loose bolt"
    assert result["byWho"] == "A. Banda"
    insert_call = next(c for c in fake.calls if c["table"] == "sheq_findings" and c["op"] == "insert")
    assert insert_call["payload"]["inspection_id"] == "i1"
    assert insert_call["payload"]["bywho"] == "A. Banda"


async def test_add_finding_to_missing_inspection_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await add_finding(
            "missing",
            FindingCreate(finding="X", requiredAction="Y", byWho="Z", byWhen="2026-08-12", section="mechanical"),
            current_user=USER,
        )
    assert exc_info.value.status_code == 404


# ─── update_finding ──────────────────────────────────────────────────────────────────

async def test_update_finding_updates_only_provided_fields(patch_supabase):
    fake = patch_supabase(findings=[_finding("f1", "i1", status="open")])
    result = await update_finding("f1", FindingUpdate(status="closed", remarks="Fixed"), current_user=USER)
    assert result["status"] == "closed"
    assert result["remarks"] == "Fixed"
    assert result["finding"] == "Guard missing"  # untouched field preserved
    update_call = next(c for c in fake.calls if c["table"] == "sheq_findings" and c["op"] == "update")
    assert update_call["payload"] == {"status": "closed", "remarks": "Fixed"}


async def test_update_finding_with_no_fields_set_returns_existing_without_writing(patch_supabase):
    fake = patch_supabase(findings=[_finding("f1", "i1")])
    result = await update_finding("f1", FindingUpdate(), current_user=USER)
    assert result["id"] == "f1"
    assert not any(c["table"] == "sheq_findings" and c["op"] == "update" for c in fake.calls)


async def test_update_finding_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await update_finding("missing", FindingUpdate(status="closed"), current_user=USER)
    assert exc_info.value.status_code == 404


# ─── delete_finding ──────────────────────────────────────────────────────────────────

async def test_delete_finding_removes_row(patch_supabase):
    fake = patch_supabase(findings=[_finding("f1", "i1")])
    result = await delete_finding("f1", current_user=USER)
    assert result == {"success": True, "message": "Finding deleted successfully"}
    assert fake.tables["sheq_findings"] == []


async def test_delete_finding_missing_is_404(patch_supabase):
    patch_supabase()
    with pytest.raises(HTTPException) as exc_info:
        await delete_finding("missing", current_user=USER)
    assert exc_info.value.status_code == 404


# ─── failure paths: insert/update returning no row, and unexpected exceptions ──────
# These confirm every handler turns a real failure into a 4xx/5xx HTTPException
# rather than silently faking a success response (see ENGINEERING_STANDARDS.md
# section 2, "never fake a 200 on failure").

async def test_create_inspection_insert_returning_nothing_is_500(patch_supabase):
    patch_supabase(fail_ops={("sheq_inspections", "insert")})
    payload = SHEQCreate(inspectors="John Doe", title="X", place="P", date="2026-08-01",
                          time="08:00", section="mechanical")
    with pytest.raises(HTTPException) as exc_info:
        await create_inspection(payload, current_user=USER)
    assert exc_info.value.status_code == 500


async def test_add_finding_insert_returning_nothing_is_500(patch_supabase):
    patch_supabase(inspections=[_insp("i1")], fail_ops={("sheq_findings", "insert")})
    with pytest.raises(HTTPException) as exc_info:
        await add_finding("i1", FindingCreate(finding="X", requiredAction="Y", byWho="Z",
                                                byWhen="2026-08-12", section="mechanical"),
                           current_user=USER)
    assert exc_info.value.status_code == 500


async def test_update_finding_update_returning_nothing_is_500(patch_supabase):
    patch_supabase(findings=[_finding("f1", "i1")], fail_ops={("sheq_findings", "update")})
    with pytest.raises(HTTPException) as exc_info:
        await update_finding("f1", FindingUpdate(status="closed"), current_user=USER)
    assert exc_info.value.status_code == 500


async def test_get_inspections_unexpected_exception_is_500_not_a_fake_empty_list(patch_supabase):
    patch_supabase(raise_on={"sheq_inspections"})
    with pytest.raises(HTTPException) as exc_info:
        await get_inspections(search=None, section=None, status=None, inspector=None,
                               from_date=None, to_date=None)
    assert exc_info.value.status_code == 500


async def test_delete_inspection_unexpected_exception_is_500(patch_supabase):
    fake = patch_supabase(inspections=[_insp("i1")])
    fake.raise_on = {"sheq_inspections"}
    # the existence check runs first and would itself raise since it also hits
    # sheq_inspections — confirms the except-Exception wrapper (not the 404
    # branch) is what's producing the error here.
    with pytest.raises(HTTPException) as exc_info:
        await delete_inspection("i1", current_user=USER)
    assert exc_info.value.status_code == 500
