# tests/test_vfl_reports.py — the VFL CRUD handlers (get_vfl_reports,
# get_observer_suggestions, get_vfl_report, create_vfl_report, update_vfl_report,
# delete_vfl_report). test_vfl_stats.py already covers get_vfl_stats and the two
# camelCase mappers; this file covers everything else, including the nested
# vfl_action_plan handling on create/update — a report's `actions` list lives in a
# separate table, joined/replaced by hand (no DB-level nested write), so the
# create/delete-then-insert/keep-existing branches are real, easy-to-break logic.
#
# Uses the "call the route coroutine directly against a fake supabase client" recipe
# from test_documents_folder_rename.py, generalized further: each table's select/
# insert/update/delete can be a static value OR a callable(payload, filters) so a
# test can make one specific call fail (e.g. only the insert on vfl_reports) without
# a full bespoke fake per scenario.

import pytest
from fastapi import HTTPException

import app.routers.vfl as vfl_mod
from app.routers.vfl import (
    get_vfl_reports, get_observer_suggestions, get_vfl_report,
    create_vfl_report, update_vfl_report, delete_vfl_report,
    VFLReportCreate, VFLReportUpdate, ActionItemCreate, ActionItemUpdate,
)


# ─── Fake supabase — records every call, per-table/per-op configurable responses ──

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state, config):
        self.table_name = table_name
        self.state = state
        self.config = config.get(table_name, {})
        self._filters = []
        self._op = "select"
        self._payload = None

    def select(self, *a, **k): return self
    def eq(self, col, val): self._filters.append((col, val)); return self
    def ilike(self, col, val): self._filters.append((col, val)); return self
    def or_(self, s): self._filters.append(("or_", s)); return self
    def gte(self, col, val): self._filters.append((f"{col}__gte", val)); return self
    def lte(self, col, val): self._filters.append((f"{col}__lte", val)); return self
    def neq(self, col, val): self._filters.append((f"{col}__neq", val)); return self
    def order(self, *a, **k): return self
    def range(self, *a, **k): return self

    @property
    def not_(self): return self
    def is_(self, col, val): self._filters.append((f"{col}__is", val)); return self

    def insert(self, data): self._op = "insert"; self._payload = data; return self
    def update(self, data): self._op = "update"; self._payload = data; return self
    def delete(self): self._op = "delete"; return self

    def execute(self):
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        raises_key = f"{self._op}_raises"
        if raises_key in self.config:
            raise self.config[raises_key]

        key = f"{self._op}_return"
        if key in self.config:
            val = self.config[key]
            return _Resp(val(self._payload, self._filters) if callable(val) else val)

        if self._op == "insert":
            payload = self._payload
            if isinstance(payload, list):
                return _Resp([{"id": "new", **p} for p in payload])
            return _Resp([{"id": "new", **(payload or {})}])
        if self._op == "update":
            return _Resp([dict(self._payload)] if self._payload else [])
        return _Resp([])


class _FakeSupabase:
    def __init__(self, state, config):
        self.state = state
        self.config = config

    def table(self, name):
        return _FakeQuery(name, self.state, self.config)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(config: dict):
        state = {"calls": []}
        monkeypatch.setattr(vfl_mod, "supabase", _FakeSupabase(state, config))
        return state
    return _patch


def _calls(state, table, op=None):
    return [c for c in state["calls"] if c["table"] == table and (op is None or c["op"] == op)]


# ─── get_vfl_reports ─────────────────────────────────────────────────────────────

async def test_get_vfl_reports_attaches_actions_per_report(patch_supabase):
    reports = [
        {"id": "r1", "observer_name": "J. Moyo", "section_choice": "Mechanical",
         "behaviour_category": "Safe Behaviour", "observation_type": "Safe Condition",
         "coaching_technique": "SBR", "status": "draft", "created_at": "t1"},
        {"id": "r2", "observer_name": "T. Ncube", "section_choice": "Electrical",
         "behaviour_category": "Unsafe Behaviour", "observation_type": "At Risk Behaviour",
         "coaching_technique": "CC", "status": "submitted", "created_at": "t2"},
    ]
    actions_by_report = {
        "r1": [{"id": "a1", "report_id": "r1", "action": "Fix guard", "responsible": "J",
                "target_date": "2026-09-01", "status": "Pending", "remarks": ""}],
        "r2": [],
    }

    def action_select(payload, filters):
        rid = dict((f if len(f) == 2 else (f[0], None)) for f in filters).get("report_id")
        return actions_by_report.get(rid, [])

    state = patch_supabase({
        "vfl_reports": {"select_return": reports},
        "vfl_action_plan": {"select_return": action_select},
    })

    result = await get_vfl_reports(
        search=None, section=None, observer=None, status=None, behaviour=None,
        from_date=None, to_date=None, limit=100, offset=0,
    )

    assert len(result) == 2
    assert result[0]["id"] == "r1"
    assert len(result[0]["actions"]) == 1
    assert result[0]["actions"][0]["action"] == "Fix guard"
    assert result[1]["actions"] == []


async def test_get_vfl_reports_applies_search_section_observer_status_behaviour_and_date_filters(patch_supabase):
    state = patch_supabase({
        "vfl_reports": {"select_return": []},
        "vfl_action_plan": {"select_return": []},
    })
    await get_vfl_reports(
        search="ladder", section="Mechanical", observer="Moyo", status="draft", behaviour="Safe Behaviour",
        from_date="2026-01-01", to_date="2026-01-31", limit=50, offset=10,
    )
    call = _calls(state, "vfl_reports", "select")[0]
    filters = dict((f[0], f[1]) if len(f) == 2 else (f[0], f[1]) for f in call["filters"])
    assert any(f[0] == "or_" for f in call["filters"])
    assert filters["section_choice"] == "Mechanical"
    assert filters["observer_name"] == "%Moyo%"
    assert filters["status"] == "draft"
    assert filters["behaviour_category"] == "Safe Behaviour"
    assert filters["date__gte"] == "2026-01-01"
    assert filters["date__lte"] == "2026-01-31"


async def test_get_vfl_reports_db_error_is_500(patch_supabase):
    patch_supabase({"vfl_reports": {"select_raises": Exception("db down")}})
    with pytest.raises(HTTPException) as exc:
        await get_vfl_reports(search=None, section=None, observer=None, status=None, behaviour=None,
                               from_date=None, to_date=None, limit=100, offset=0)
    assert exc.value.status_code == 500


# ─── get_observer_suggestions — thin wiring test (distinct_suggestions itself is
#     already exhaustively covered in test_db_helpers.py) ─────────────────────────

async def test_get_observer_suggestions_queries_the_right_table_and_column(patch_supabase):
    state = patch_supabase({
        "vfl_reports": {"select_return": [{"observer_name": "B. Ncube"}, {"observer_name": "A. Moyo"}]},
    })
    result = await get_observer_suggestions(search=None)
    assert result == ["A. Moyo", "B. Ncube"]
    assert _calls(state, "vfl_reports", "select")


# ─── get_vfl_report ──────────────────────────────────────────────────────────────

async def test_get_vfl_report_happy_path_includes_actions(patch_supabase):
    patch_supabase({
        "vfl_reports": {"select_return": [{"id": "r1", "observer_name": "J. Moyo", "status": "draft"}]},
        "vfl_action_plan": {"select_return": [{"id": "a1", "report_id": "r1", "action": "Fix it",
                                                "responsible": "J", "target_date": "2026-09-01"}]},
    })
    result = await get_vfl_report("r1")
    assert result["id"] == "r1"
    assert len(result["actions"]) == 1


async def test_get_vfl_report_not_found_is_404(patch_supabase):
    patch_supabase({"vfl_reports": {"select_return": []}})
    with pytest.raises(HTTPException) as exc:
        await get_vfl_report("missing")
    assert exc.value.status_code == 404


async def test_get_vfl_report_db_error_is_500(patch_supabase):
    patch_supabase({"vfl_reports": {"select_raises": Exception("boom")}})
    with pytest.raises(HTTPException) as exc:
        await get_vfl_report("r1")
    assert exc.value.status_code == 500


# ─── create_vfl_report ───────────────────────────────────────────────────────────

def _valid_report_kwargs(**overrides):
    base = dict(
        observerName="J. Moyo", designation="Foreman", sectionChoice="Mechanical",
        departmentSection="Plant", date="2026-08-30", time="09:00",
        behaviourCategory="Safe Behaviour", observationType="Safe Condition",
        description="Observed correct lockout procedure being followed.",
        coachingTechnique="SBR", status="draft",
    )
    base.update(overrides)
    return base


async def test_create_vfl_report_happy_path_with_actions(patch_supabase):
    report = VFLReportCreate(
        **_valid_report_kwargs(),
        actions=[ActionItemCreate(action="Replace guard", responsible="T. Ncube", targetDate="2026-09-05")],
    )
    state = patch_supabase({
        "vfl_reports": {"insert_return": lambda payload, f: [dict(payload)]},
        "vfl_action_plan": {"insert_return": lambda payload, f: [dict(p) for p in payload]},
    })
    result = await create_vfl_report(report, current_user={"user_id": "u1"})

    assert result["observerName"] == "J. Moyo"
    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "Replace guard"

    report_insert = _calls(state, "vfl_reports", "insert")[0]["payload"]
    assert report_insert["observer_name"] == "J. Moyo"
    assert report_insert["section_choice"] == "Mechanical"

    action_insert = _calls(state, "vfl_action_plan", "insert")[0]["payload"]
    assert action_insert[0]["responsible"] == "T. Ncube"
    assert action_insert[0]["target_date"] == "2026-09-05"


async def test_create_vfl_report_without_actions_returns_empty_actions_list(patch_supabase):
    report = VFLReportCreate(**_valid_report_kwargs())
    state = patch_supabase({"vfl_reports": {"insert_return": lambda payload, f: [dict(payload)]}})
    result = await create_vfl_report(report, current_user={"user_id": "u1"})
    assert result["actions"] == []
    assert not _calls(state, "vfl_action_plan", "insert")


async def test_create_vfl_report_submitted_status_sets_submitted_at(patch_supabase):
    report = VFLReportCreate(**_valid_report_kwargs(status="submitted"))
    state = patch_supabase({"vfl_reports": {"insert_return": lambda payload, f: [dict(payload)]}})
    await create_vfl_report(report, current_user={"user_id": "u1"})
    payload = _calls(state, "vfl_reports", "insert")[0]["payload"]
    assert payload["submitted_at"] is not None


async def test_create_vfl_report_draft_status_leaves_submitted_at_none(patch_supabase):
    report = VFLReportCreate(**_valid_report_kwargs(status="draft"))
    state = patch_supabase({"vfl_reports": {"insert_return": lambda payload, f: [dict(payload)]}})
    await create_vfl_report(report, current_user={"user_id": "u1"})
    payload = _calls(state, "vfl_reports", "insert")[0]["payload"]
    assert payload["submitted_at"] is None


async def test_create_vfl_report_insert_returning_nothing_is_500(patch_supabase):
    report = VFLReportCreate(**_valid_report_kwargs())
    patch_supabase({"vfl_reports": {"insert_return": []}})
    with pytest.raises(HTTPException) as exc:
        await create_vfl_report(report, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_create_vfl_report_unexpected_db_error_is_500(patch_supabase):
    # A non-HTTPException raised mid-operation (not the "insert returned nothing"
    # case above) must still be caught by the generic `except Exception` and turned
    # into a real 500, never bubble up as a raw 500 with no detail.
    report = VFLReportCreate(**_valid_report_kwargs())
    patch_supabase({"vfl_reports": {"insert_raises": Exception("connection reset")}})
    with pytest.raises(HTTPException) as exc:
        await create_vfl_report(report, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500
    assert "connection reset" in exc.value.detail


# ─── update_vfl_report ───────────────────────────────────────────────────────────

async def test_update_vfl_report_not_found_is_404(patch_supabase):
    patch_supabase({"vfl_reports": {"select_return": []}})
    with pytest.raises(HTTPException) as exc:
        await update_vfl_report("missing", VFLReportUpdate(observerName="New"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 404


async def test_update_vfl_report_unexpected_db_error_on_lookup_is_500(patch_supabase):
    patch_supabase({"vfl_reports": {"select_raises": Exception("connection reset")}})
    with pytest.raises(HTTPException) as exc:
        await update_vfl_report("r1", VFLReportUpdate(observerName="New"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_update_vfl_report_field_update_maps_to_snake_case(patch_supabase):
    existing = {"id": "r1", "observer_name": "Old Name", "status": "draft"}
    state = patch_supabase({
        "vfl_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "vfl_action_plan": {"select_return": []},
    })
    result = await update_vfl_report("r1", VFLReportUpdate(observerName="New Name"), current_user={"user_id": "u1"})
    assert result["observerName"] == "New Name"

    update_payload = _calls(state, "vfl_reports", "update")[0]["payload"]
    assert update_payload["observer_name"] == "New Name"
    assert "updated_at" in update_payload


async def test_update_vfl_report_status_to_submitted_sets_submitted_at(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "vfl_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "vfl_action_plan": {"select_return": []},
    })
    await update_vfl_report("r1", VFLReportUpdate(status="submitted"), current_user={"user_id": "u1"})
    update_payload = _calls(state, "vfl_reports", "update")[0]["payload"]
    assert update_payload["submitted_at"] is not None


async def test_update_vfl_report_replaces_actions_deleting_then_inserting(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "vfl_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "vfl_action_plan": {"insert_return": lambda payload, f: [dict(p) for p in payload]},
    })
    new_actions = [ActionItemUpdate(action="New action", responsible="J", targetDate="2026-09-10",
                                     status="Pending", completedDate=None, remarks="")]
    result = await update_vfl_report("r1", VFLReportUpdate(actions=new_actions), current_user={"user_id": "u1"})

    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "New action"

    action_calls = _calls(state, "vfl_action_plan")
    assert action_calls[0]["op"] == "delete"
    assert ("report_id", "r1") in action_calls[0]["filters"]
    assert action_calls[1]["op"] == "insert"


async def test_update_vfl_report_incomplete_action_items_are_silently_dropped(patch_supabase):
    # An action item missing action/responsible/targetDate is skipped rather than
    # inserted with blank required fields — real branch logic in the loop.
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "vfl_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "vfl_action_plan": {},
    })
    incomplete_actions = [ActionItemUpdate(action=None, responsible="J", targetDate="2026-09-10")]
    result = await update_vfl_report("r1", VFLReportUpdate(actions=incomplete_actions), current_user={"user_id": "u1"})
    assert result["actions"] == []
    assert not _calls(state, "vfl_action_plan", "insert")


async def test_update_vfl_report_actions_explicitly_cleared_to_empty_list(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "vfl_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "vfl_action_plan": {},
    })
    result = await update_vfl_report("r1", VFLReportUpdate(actions=[]), current_user={"user_id": "u1"})
    assert result["actions"] == []
    assert _calls(state, "vfl_action_plan", "delete")
    assert not _calls(state, "vfl_action_plan", "insert")


async def test_update_vfl_report_actions_omitted_keeps_existing_actions(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "vfl_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "vfl_action_plan": {"select_return": [{"id": "a1", "report_id": "r1", "action": "Existing"}]},
    })
    result = await update_vfl_report("r1", VFLReportUpdate(observerName="Renamed"), current_user={"user_id": "u1"})
    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "Existing"
    assert not _calls(state, "vfl_action_plan", "delete")


async def test_update_vfl_report_update_returning_nothing_is_500(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    patch_supabase({
        "vfl_reports": {"select_return": [existing], "update_return": []},
    })
    with pytest.raises(HTTPException) as exc:
        await update_vfl_report("r1", VFLReportUpdate(observerName="New"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── delete_vfl_report ───────────────────────────────────────────────────────────

async def test_delete_vfl_report_happy_path(patch_supabase):
    state = patch_supabase({"vfl_reports": {"select_return": [{"id": "r1"}]}})
    result = await delete_vfl_report("r1", current_user={"user_id": "u1", "role": "manager"})
    assert result == {"success": True, "message": "Report deleted successfully"}
    assert _calls(state, "vfl_reports", "delete")


async def test_delete_vfl_report_not_found_is_404(patch_supabase):
    patch_supabase({"vfl_reports": {"select_return": []}})
    with pytest.raises(HTTPException) as exc:
        await delete_vfl_report("missing", current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 404


async def test_delete_vfl_report_db_error_is_500(patch_supabase):
    patch_supabase({"vfl_reports": {"select_return": [{"id": "r1"}], "delete_raises": Exception("boom")}})
    with pytest.raises(HTTPException) as exc:
        await delete_vfl_report("r1", current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 500
