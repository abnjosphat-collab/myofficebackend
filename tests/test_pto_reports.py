# tests/test_pto_reports.py — the PTO CRUD handlers (get_pto_reports,
# get_observer_suggestions, get_pto_report, create_pto_report, update_pto_report,
# delete_pto_report). test_pto_stats.py already covers get_pto_stats/highRiskCount and
# the mapper's nested default shapes; this file covers everything else, including the
# nested pto_action_plan handling on create/update (a separate table, replaced by hand
# on update — same shape as vfl.py's action plan, but PTO action items carry a `no`
# ordering field with a real asymmetry worth locking in: create() inserts the caller's
# own `action.no` values verbatim, while update() always renumbers 1..N sequentially —
# ActionPlanItemUpdate has no `no` field at all, so the caller can't supply one on
# update even if they wanted to. Documented here, not "fixed" — it's the only
# consistent choice given the model shapes.
#
# Same fake-supabase recipe as test_vfl_reports.py (bespoke per file, not shared,
# per this project's conftest.py convention for business-logic-heavy routers).

import pytest
from pydantic import ValidationError
from fastapi import HTTPException

import app.routers.pto as pto_mod
from app.routers.pto import (
    get_pto_reports, get_observer_suggestions, get_pto_report,
    create_pto_report, update_pto_report, delete_pto_report,
    PTOReportCreate, PTOReportUpdate, ActionPlanItemCreate, ActionPlanItemUpdate,
    TimeOnJob, Notification, Reasons, Procedures, RiskAssessment, SuggestedRemedies,
)


# ─── Yes/No-only field validators (Notification/Procedures/RiskAssessment/
#     SuggestedRemedies all reject anything other than the literal "Yes"/"No") ──

def test_notification_rejects_non_yes_no_value():
    with pytest.raises(ValidationError):
        Notification(toldInAdvance="Maybe")


def test_procedures_rejects_non_yes_no_value():
    with pytest.raises(ValidationError):
        Procedures(hasProcedure="Sometimes")


def test_risk_assessment_rejects_non_yes_no_value():
    with pytest.raises(ValidationError):
        RiskAssessment(made="Partially")


def test_suggested_remedies_rejects_non_yes_no_value():
    with pytest.raises(ValidationError):
        SuggestedRemedies(retraining="TBD")


def test_suggested_remedies_accepts_explicit_valid_value():
    # Field validators only fire on an explicitly-supplied value, not on a
    # silently-applied default (pydantic v2) — the other three Yes/No models above
    # get their "accept" path exercised via _valid_report_kwargs already; this one
    # only ever uses SuggestedRemedies() with all defaults there, so needs its own.
    r = SuggestedRemedies(retraining="Yes")
    assert r.retraining == "Yes"


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
        monkeypatch.setattr(pto_mod, "supabase", _FakeSupabase(state, config))
        return state
    return _patch


def _calls(state, table, op=None):
    return [c for c in state["calls"] if c["table"] == table and (op is None or c["op"] == op)]


def _valid_report_kwargs(**overrides):
    base = dict(
        date="2026-08-30", observerName="J. Moyo", section="Mechanical",
        deptSectionContractor="Plant", workerName="T. Ncube", occupation="Fitter",
        jobTaskObserved="Bearing replacement", sheqRefNo="SHEQ-1",
        observationType="Initial",
        timeOnJob=TimeOnJob(months="6", years="1"),
        notification=Notification(toldInAdvance="No"),
        reasons=Reasons(monthly=True),
        procedures=Procedures(hasProcedure="Yes", familiarWithProcedure="Yes"),
        riskAssessment=RiskAssessment(made="Yes", identified="Yes", effective="Yes"),
        suggestedRemedies=SuggestedRemedies(),
        observationScope="All", followUpNeeded="No", status="draft",
    )
    base.update(overrides)
    return base


# ─── get_pto_reports ─────────────────────────────────────────────────────────────

async def test_get_pto_reports_attaches_action_plan_per_report(patch_supabase):
    reports = [
        {"id": "r1", "observer_name": "J. Moyo", "section": "Mechanical", "status": "draft"},
        {"id": "r2", "observer_name": "T. Ncube", "section": "Electrical", "status": "submitted"},
    ]
    actions_by_report = {
        "r1": [{"id": "a1", "report_id": "r1", "no": 1, "action": "Fix guard", "by_whom": "J", "by_when": "2026-09-01"}],
        "r2": [],
    }

    def action_select(payload, filters):
        rid = dict(filters).get("report_id")
        return actions_by_report.get(rid, [])

    state = patch_supabase({
        "pto_reports": {"select_return": reports},
        "pto_action_plan": {"select_return": action_select},
    })

    result = await get_pto_reports(
        search=None, section=None, observer=None, status=None,
        from_date=None, to_date=None, limit=100, offset=0,
    )

    assert len(result) == 2
    assert result[0]["id"] == "r1"
    assert len(result[0]["actionPlan"]) == 1
    assert result[1]["actionPlan"] == []


async def test_get_pto_reports_applies_search_section_observer_status_and_date_filters(patch_supabase):
    state = patch_supabase({
        "pto_reports": {"select_return": []},
        "pto_action_plan": {"select_return": []},
    })
    await get_pto_reports(
        search="bearing", section="Mechanical", observer="Moyo", status="draft",
        from_date="2026-01-01", to_date="2026-01-31", limit=50, offset=10,
    )
    call = _calls(state, "pto_reports", "select")[0]
    filters = dict(call["filters"])
    assert any(f[0] == "or_" for f in call["filters"])
    assert filters["section"] == "Mechanical"
    assert filters["observer_name"] == "%Moyo%"
    assert filters["status"] == "draft"
    assert filters["date__gte"] == "2026-01-01"
    assert filters["date__lte"] == "2026-01-31"


async def test_get_pto_reports_db_error_is_500(patch_supabase):
    patch_supabase({"pto_reports": {"select_raises": Exception("db down")}})
    with pytest.raises(HTTPException) as exc:
        await get_pto_reports(search=None, section=None, observer=None, status=None,
                               from_date=None, to_date=None, limit=100, offset=0)
    assert exc.value.status_code == 500


# ─── get_observer_suggestions — thin wiring test ────────────────────────────────

async def test_get_observer_suggestions_queries_the_right_table_and_column(patch_supabase):
    state = patch_supabase({
        "pto_reports": {"select_return": [{"observer_name": "B. Ncube"}, {"observer_name": "A. Moyo"}]},
    })
    result = await get_observer_suggestions(search=None)
    assert result == ["A. Moyo", "B. Ncube"]
    assert _calls(state, "pto_reports", "select")


# ─── get_pto_report ──────────────────────────────────────────────────────────────

async def test_get_pto_report_happy_path_includes_action_plan(patch_supabase):
    patch_supabase({
        "pto_reports": {"select_return": [{"id": "r1", "observer_name": "J. Moyo", "status": "draft"}]},
        "pto_action_plan": {"select_return": [{"id": "a1", "report_id": "r1", "no": 1, "action": "Fix it",
                                                "by_whom": "J", "by_when": "2026-09-01"}]},
    })
    result = await get_pto_report("r1")
    assert result["id"] == "r1"
    assert len(result["actionPlan"]) == 1


async def test_get_pto_report_not_found_is_404(patch_supabase):
    patch_supabase({"pto_reports": {"select_return": []}})
    with pytest.raises(HTTPException) as exc:
        await get_pto_report("missing")
    assert exc.value.status_code == 404


async def test_get_pto_report_db_error_is_500(patch_supabase):
    patch_supabase({"pto_reports": {"select_raises": Exception("boom")}})
    with pytest.raises(HTTPException) as exc:
        await get_pto_report("r1")
    assert exc.value.status_code == 500


# ─── create_pto_report ───────────────────────────────────────────────────────────

async def test_create_pto_report_happy_path_with_action_plan_preserves_caller_supplied_no(patch_supabase):
    report = PTOReportCreate(
        **_valid_report_kwargs(),
        actionPlan=[
            ActionPlanItemCreate(no=5, action="Replace guard", byWhom="T. Ncube", byWhen="2026-09-05"),
        ],
    )
    state = patch_supabase({
        "pto_reports": {"insert_return": lambda payload, f: [dict(payload)]},
        "pto_action_plan": {"insert_return": lambda payload, f: [dict(p) for p in payload]},
    })
    result = await create_pto_report(report, current_user={"user_id": "u1"})

    assert result["observerName"] == "J. Moyo"
    assert len(result["actionPlan"]) == 1
    assert result["actionPlan"][0]["no"] == 5  # caller's own numbering preserved on create

    report_insert = _calls(state, "pto_reports", "insert")[0]["payload"]
    assert report_insert["observer_name"] == "J. Moyo"
    assert report_insert["time_on_job"] == {"months": "6", "years": "1"}
    assert report_insert["risk_assessment"] == {"made": "Yes", "identified": "Yes", "effective": "Yes"}

    action_insert = _calls(state, "pto_action_plan", "insert")[0]["payload"]
    assert action_insert[0]["no"] == 5
    assert action_insert[0]["by_whom"] == "T. Ncube"


async def test_create_pto_report_without_action_plan_returns_empty_list(patch_supabase):
    report = PTOReportCreate(**_valid_report_kwargs())
    state = patch_supabase({"pto_reports": {"insert_return": lambda payload, f: [dict(payload)]}})
    result = await create_pto_report(report, current_user={"user_id": "u1"})
    assert result["actionPlan"] == []
    assert not _calls(state, "pto_action_plan", "insert")


async def test_create_pto_report_submitted_status_sets_submitted_at(patch_supabase):
    report = PTOReportCreate(**_valid_report_kwargs(status="submitted"))
    state = patch_supabase({"pto_reports": {"insert_return": lambda payload, f: [dict(payload)]}})
    await create_pto_report(report, current_user={"user_id": "u1"})
    payload = _calls(state, "pto_reports", "insert")[0]["payload"]
    assert payload["submitted_at"] is not None


async def test_create_pto_report_insert_returning_nothing_is_500(patch_supabase):
    report = PTOReportCreate(**_valid_report_kwargs())
    patch_supabase({"pto_reports": {"insert_return": []}})
    with pytest.raises(HTTPException) as exc:
        await create_pto_report(report, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_create_pto_report_unexpected_db_error_is_500(patch_supabase):
    report = PTOReportCreate(**_valid_report_kwargs())
    patch_supabase({"pto_reports": {"insert_raises": Exception("connection reset")}})
    with pytest.raises(HTTPException) as exc:
        await create_pto_report(report, current_user={"user_id": "u1"})
    assert exc.value.status_code == 500
    assert "connection reset" in exc.value.detail


# ─── update_pto_report ───────────────────────────────────────────────────────────

async def test_update_pto_report_not_found_is_404(patch_supabase):
    patch_supabase({"pto_reports": {"select_return": []}})
    with pytest.raises(HTTPException) as exc:
        await update_pto_report("missing", PTOReportUpdate(observerName="New"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 404


async def test_update_pto_report_unexpected_db_error_on_lookup_is_500(patch_supabase):
    patch_supabase({"pto_reports": {"select_raises": Exception("connection reset")}})
    with pytest.raises(HTTPException) as exc:
        await update_pto_report("r1", PTOReportUpdate(observerName="New"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_update_pto_report_field_update_maps_to_snake_case(patch_supabase):
    existing = {"id": "r1", "observer_name": "Old Name", "status": "draft"}
    state = patch_supabase({
        "pto_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "pto_action_plan": {"select_return": []},
    })
    result = await update_pto_report("r1", PTOReportUpdate(observerName="New Name"), current_user={"user_id": "u1"})
    assert result["observerName"] == "New Name"
    update_payload = _calls(state, "pto_reports", "update")[0]["payload"]
    assert update_payload["observer_name"] == "New Name"
    assert "updated_at" in update_payload


async def test_update_pto_report_nested_model_fields_are_converted_to_dict(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "pto_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "pto_action_plan": {"select_return": []},
    })
    new_risk = RiskAssessment(made="No", identified="Yes", effective="Yes")
    await update_pto_report("r1", PTOReportUpdate(riskAssessment=new_risk), current_user={"user_id": "u1"})
    update_payload = _calls(state, "pto_reports", "update")[0]["payload"]
    assert update_payload["risk_assessment"] == {"made": "No", "identified": "Yes", "effective": "Yes"}


async def test_update_pto_report_status_to_submitted_sets_submitted_at(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "pto_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "pto_action_plan": {"select_return": []},
    })
    await update_pto_report("r1", PTOReportUpdate(status="submitted"), current_user={"user_id": "u1"})
    update_payload = _calls(state, "pto_reports", "update")[0]["payload"]
    assert update_payload["submitted_at"] is not None


async def test_update_pto_report_replaces_action_plan_renumbering_sequentially(patch_supabase):
    # ActionPlanItemUpdate has no `no` field — update() always renumbers 1..N,
    # unlike create() which preserves the caller's own numbering. Documented, not a bug.
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "pto_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "pto_action_plan": {"insert_return": lambda payload, f: [dict(p) for p in payload]},
    })
    new_actions = [
        ActionPlanItemUpdate(action="First", byWhom="J", byWhen="2026-09-10", status="Pending"),
        ActionPlanItemUpdate(action="Second", byWhom="T", byWhen="2026-09-11", status="Pending"),
    ]
    result = await update_pto_report("r1", PTOReportUpdate(actionPlan=new_actions), current_user={"user_id": "u1"})

    assert [a["no"] for a in result["actionPlan"]] == [1, 2]
    action_calls = _calls(state, "pto_action_plan")
    assert action_calls[0]["op"] == "delete"
    assert ("report_id", "r1") in action_calls[0]["filters"]
    assert action_calls[1]["op"] == "insert"
    assert [p["no"] for p in action_calls[1]["payload"]] == [1, 2]


async def test_update_pto_report_incomplete_action_items_are_silently_dropped(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "pto_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "pto_action_plan": {},
    })
    incomplete = [ActionPlanItemUpdate(action=None, byWhom="J", byWhen="2026-09-10")]
    result = await update_pto_report("r1", PTOReportUpdate(actionPlan=incomplete), current_user={"user_id": "u1"})
    assert result["actionPlan"] == []
    assert not _calls(state, "pto_action_plan", "insert")


async def test_update_pto_report_action_plan_explicitly_cleared_to_empty_list(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "pto_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "pto_action_plan": {},
    })
    result = await update_pto_report("r1", PTOReportUpdate(actionPlan=[]), current_user={"user_id": "u1"})
    assert result["actionPlan"] == []
    assert _calls(state, "pto_action_plan", "delete")
    assert not _calls(state, "pto_action_plan", "insert")


async def test_update_pto_report_action_plan_omitted_keeps_existing(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    state = patch_supabase({
        "pto_reports": {
            "select_return": [existing],
            "update_return": lambda payload, f: [{**existing, **payload}],
        },
        "pto_action_plan": {"select_return": [{"id": "a1", "report_id": "r1", "no": 1, "action": "Existing"}]},
    })
    result = await update_pto_report("r1", PTOReportUpdate(observerName="Renamed"), current_user={"user_id": "u1"})
    assert len(result["actionPlan"]) == 1
    assert result["actionPlan"][0]["action"] == "Existing"
    assert not _calls(state, "pto_action_plan", "delete")


async def test_update_pto_report_update_returning_nothing_is_500(patch_supabase):
    existing = {"id": "r1", "status": "draft"}
    patch_supabase({
        "pto_reports": {"select_return": [existing], "update_return": []},
    })
    with pytest.raises(HTTPException) as exc:
        await update_pto_report("r1", PTOReportUpdate(observerName="New"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── delete_pto_report ───────────────────────────────────────────────────────────

async def test_delete_pto_report_happy_path(patch_supabase):
    state = patch_supabase({"pto_reports": {"select_return": [{"id": "r1"}]}})
    result = await delete_pto_report("r1", current_user={"user_id": "u1", "role": "manager"})
    assert result == {"success": True, "message": "Report deleted successfully"}
    assert _calls(state, "pto_reports", "delete")


async def test_delete_pto_report_not_found_is_404(patch_supabase):
    patch_supabase({"pto_reports": {"select_return": []}})
    with pytest.raises(HTTPException) as exc:
        await delete_pto_report("missing", current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 404


async def test_delete_pto_report_db_error_is_500(patch_supabase):
    patch_supabase({"pto_reports": {"select_return": [{"id": "r1"}], "delete_raises": Exception("boom")}})
    with pytest.raises(HTTPException) as exc:
        await delete_pto_report("r1", current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 500
