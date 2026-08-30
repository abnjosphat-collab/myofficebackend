# tests/test_pachedu_crud.py — pachedu.py's report CRUD + suggestions endpoint had zero
# tests beyond stats (test_pachedu_stats.py). Covers the camelCase<->snake_case column
# mapping round-trip (map_db_pachedu_to_camel + the field_mapping table in
# update_pachedu_report), the search/section/dept/status/date-range filters, the
# submitted_at side-effect (set on create when status="submitted", and on update when a
# PATCH changes status to "submitted"), and 404s.

import pytest

import app.routers.pachedu as pachedu_mod
from app.routers.pachedu import (
    PacheduReportCreate, PacheduReportUpdate,
    get_pachedu_reports, get_pachedu_report, create_pachedu_report,
    update_pachedu_report, delete_pachedu_report, get_department_suggestions,
)
from fastapi import HTTPException


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Chainable select/eq/ilike/gte/lte/or_/order/range + insert/update/delete over an
    in-memory `pachedu_reports` table, keyed by id (a uuid string here)."""
    def __init__(self, table_name, state):
        self.table_name = table_name
        self.state = state
        self._filters = []       # list of (col, val) eq filters
        self._ilike = []         # list of (col, substr)
        self._or_expr = None
        self._gte = []
        self._lte = []
        self._mode = "select"
        self._payload = None

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def ilike(self, col, pattern):
        self._ilike.append((col, pattern.strip("%")))
        return self

    def or_(self, expr):
        self._or_expr = expr
        return self

    def gte(self, col, val):
        self._gte.append((col, val))
        return self

    def lte(self, col, val):
        self._lte.append((col, val))
        return self

    def neq(self, *a, **k):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def insert(self, data):
        self._mode = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._mode = "update"
        self._payload = data
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def execute(self):
        table = self.state.setdefault(self.table_name, [])
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "mode": self._mode, "filters": list(self._filters), "payload": self._payload}
        )
        if self._mode == "insert":
            table.append(dict(self._payload))
            return _Resp([dict(self._payload)])

        matches = [r for r in table if all(r.get(c) == v for c, v in self._filters)]
        for col, sub in self._ilike:
            matches = [r for r in matches if sub.lower() in (r.get(col) or "").lower()]
        for col, val in self._gte:
            matches = [r for r in matches if (r.get(col) or "") >= val]
        for col, val in self._lte:
            matches = [r for r in matches if (r.get(col) or "") <= val]
        if self._or_expr:
            # search across observer_name/location/activity_observed/what_did_you_see/dept
            cols, term = [], None
            for part in self._or_expr.split(","):
                col, _, pattern = part.partition(".ilike.")
                cols.append(col)
                if term is None:
                    term = pattern.strip("%").lower()
            matches = [r for r in table if any(term in (r.get(c) or "").lower() for c in cols)]

        if self._mode == "select":
            return _Resp(matches)
        if self._mode == "update":
            for r in matches:
                r.update(self._payload)
            return _Resp(matches)
        if self._mode == "delete":
            for r in matches:
                table.remove(r)
            return _Resp(matches)
        return _Resp([])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(name, self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {}

    def _patch(reports=None):
        state["pachedu_reports"] = list(reports or [])
        monkeypatch.setattr(pachedu_mod, "supabase", _FakeSupabase(state))
        return state

    return _patch


def _user():
    return {"user_id": "u-1", "email": "u@x.com", "role": "user"}


def _manager():
    return {"user_id": "m-1", "email": "m@x.com", "role": "manager"}


def _db_report(**overrides):
    base = dict(
        id="r1", location="Shaft 3", date="2026-08-01", activity_observed="Walking",
        what_did_you_see="No PPE", reasons="", behaviour_type="Intentional",
        impacts=["safety"], what_did_you_do="Stopped work", observer_name="J. Moyo",
        dept="Engineering", sdwt="", section_choice="Mechanical", checklist=["ppe"],
        status="draft", created_at="2026-08-01T00:00:00", updated_at=None, submitted_at=None,
    )
    base.update(overrides)
    return base


# ─── get_pachedu_reports ─────────────────────────────────────────────────────────────

async def test_list_no_filters_maps_to_camel_case(patch_supabase):
    patch_supabase([_db_report()])
    result = await get_pachedu_reports(
        search=None, section=None, dept=None, status=None, from_date=None, to_date=None,
        limit=100, offset=0,
    )
    assert len(result) == 1
    assert result[0]["activityObserved"] == "Walking"
    assert result[0]["whatDidYouSee"] == "No PPE"
    assert result[0]["sectionChoice"] == "Mechanical"


async def test_list_filters_by_section(patch_supabase):
    patch_supabase([
        _db_report(id="r1", section_choice="Mechanical"),
        _db_report(id="r2", section_choice="Electrical"),
    ])
    result = await get_pachedu_reports(
        search=None, section="Electrical", dept=None, status=None, from_date=None, to_date=None,
        limit=100, offset=0,
    )
    assert [r["id"] for r in result] == ["r2"]


async def test_list_filters_by_status(patch_supabase):
    patch_supabase([
        _db_report(id="r1", status="draft"),
        _db_report(id="r2", status="submitted"),
    ])
    result = await get_pachedu_reports(
        search=None, section=None, dept=None, status="submitted", from_date=None, to_date=None,
        limit=100, offset=0,
    )
    assert [r["id"] for r in result] == ["r2"]


async def test_list_search_matches_across_multiple_columns(patch_supabase):
    patch_supabase([
        _db_report(id="r1", observer_name="Alice", location="Shaft 1"),
        _db_report(id="r2", observer_name="Bob", location="Shaft 2"),
    ])
    result = await get_pachedu_reports(
        search="Alice", section=None, dept=None, status=None, from_date=None, to_date=None,
        limit=100, offset=0,
    )
    assert [r["id"] for r in result] == ["r1"]


async def test_list_date_range_filter(patch_supabase):
    patch_supabase([
        _db_report(id="r1", date="2026-01-01"),
        _db_report(id="r2", date="2026-06-01"),
        _db_report(id="r3", date="2026-12-01"),
    ])
    result = await get_pachedu_reports(
        search=None, section=None, dept=None, status=None,
        from_date="2026-05-01", to_date="2026-11-01", limit=100, offset=0,
    )
    assert [r["id"] for r in result] == ["r2"]


# ─── get_pachedu_report ──────────────────────────────────────────────────────────────

async def test_get_single_found(patch_supabase):
    patch_supabase([_db_report(id="r1")])
    result = await get_pachedu_report("r1")
    assert result["id"] == "r1"
    assert result["behaviourType"] == "Intentional"


async def test_get_single_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await get_pachedu_report("missing")
    assert exc.value.status_code == 404


# ─── create_pachedu_report ───────────────────────────────────────────────────────────

async def test_create_maps_camel_to_snake_and_generates_id(patch_supabase):
    patch_supabase([])
    report = PacheduReportCreate(
        location="Shaft 3", date="2026-08-01", activityObserved="Walking",
        whatDidYouSee="No PPE", behaviourType="Intentional", whatDidYouDo="Stopped work",
        sectionChoice="Mechanical",
    )
    result = await create_pachedu_report(report, current_user=_user())
    assert result["activityObserved"] == "Walking"
    assert result["id"]  # a uuid was generated
    assert result["status"] == "draft"
    assert result["submitted_at"] is None  # not submitted yet


async def test_create_with_submitted_status_sets_submitted_at(patch_supabase):
    patch_supabase([])
    report = PacheduReportCreate(
        location="Shaft 3", date="2026-08-01", activityObserved="Walking",
        whatDidYouSee="No PPE", behaviourType="Intentional", whatDidYouDo="Stopped work",
        sectionChoice="Mechanical", status="submitted",
    )
    result = await create_pachedu_report(report, current_user=_user())
    assert result["submitted_at"] is not None


async def test_create_rejects_invalid_behaviour_type():
    with pytest.raises(Exception):
        PacheduReportCreate(
            location="Shaft 3", date="2026-08-01", activityObserved="Walking",
            whatDidYouSee="No PPE", behaviourType="Sideways", whatDidYouDo="Stopped work",
            sectionChoice="Mechanical",
        )


# ─── update_pachedu_report ───────────────────────────────────────────────────────────

async def test_update_maps_camel_field_to_snake_column(patch_supabase):
    state = patch_supabase([_db_report(id="r1", location="Old Site")])
    result = await update_pachedu_report("r1", PacheduReportUpdate(location="New Site"), current_user=_user())
    assert result["location"] == "New Site"
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert update_calls[0]["payload"]["location"] == "New Site"


async def test_update_to_submitted_sets_submitted_at(patch_supabase):
    state = patch_supabase([_db_report(id="r1", status="draft", submitted_at=None)])
    await update_pachedu_report("r1", PacheduReportUpdate(status="submitted"), current_user=_user())
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert update_calls[0]["payload"]["status"] == "submitted"
    assert update_calls[0]["payload"]["submitted_at"] is not None


async def test_update_no_fields_sent_still_bumps_updated_at_only(patch_supabase):
    # NOTE: the handler builds `data_to_update["updated_at"] = ...` unconditionally
    # *before* checking `if data_to_update:` to decide whether to skip the DB write —
    # so that check can never actually be false, and the `else: updated_report =
    # existing_row` branch a few lines down is dead code. An empty PATCH body still
    # issues an UPDATE (bumping only updated_at), rather than being a true no-op.
    # Documented here as existing behavior, not fixed: harmless (no field is
    # corrupted, the call still succeeds), unlike the work-order 409 dead-code bug
    # fixed in this pass, so left alone per the "narrow, obviously-correct fixes
    # only" instruction.
    state = patch_supabase([_db_report(id="r1", location="Same Site")])
    result = await update_pachedu_report("r1", PacheduReportUpdate(), current_user=_user())
    assert result["location"] == "Same Site"
    update_calls = [c for c in state["calls"] if c["mode"] == "update"]
    assert len(update_calls) == 1
    assert set(update_calls[0]["payload"].keys()) == {"updated_at"}


async def test_update_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await update_pachedu_report("missing", PacheduReportUpdate(location="X"), current_user=_user())
    assert exc.value.status_code == 404


# ─── delete_pachedu_report ───────────────────────────────────────────────────────────

async def test_delete_happy_path(patch_supabase):
    state = patch_supabase([_db_report(id="r1")])
    result = await delete_pachedu_report("r1", current_user=_manager())
    assert result == {"success": True, "message": "Report deleted successfully"}
    assert state["pachedu_reports"] == []


async def test_delete_not_found_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as exc:
        await delete_pachedu_report("missing", current_user=_manager())
    assert exc.value.status_code == 404


# ─── get_department_suggestions ──────────────────────────────────────────────────────

async def test_department_suggestions_returns_sorted_distinct_values(patch_supabase):
    patch_supabase([
        _db_report(id="r1", dept="Engineering"),
        _db_report(id="r2", dept="Operations"),
        _db_report(id="r3", dept="Engineering"),
    ])
    result = await get_department_suggestions(search=None)
    assert result == ["Engineering", "Operations"]
