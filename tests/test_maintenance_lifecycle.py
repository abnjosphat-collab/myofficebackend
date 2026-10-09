# tests/test_maintenance_lifecycle.py — slice 4 of the Maintenance rebuild: the status rules, the permit gate,
# signatures and the foreman sign-off. Route coroutines are called directly against the in-memory fake supabase.

import pytest
from fastapi import HTTPException

import app.maintenance_rules as rules
from app.routers.maintenance import (
    WorkOrderSignoff, WorkOrderTransition, WorkOrderUpdate,
    get_work_order_transitions, sign_off_work_order, transition_work_order, update_work_order,
)
from tests.test_maintenance_work_orders import (  # noqa: F401  (fixtures and builders are shared)
    _manager, _no_redis, _user, patch_supabase,
)

SIG = "data:image/png;base64," + "A" * 60
LIFECYCLE = {"permits": {}, "started_at": None, "completed_at": None, "artisan_signed_by": None,
             "artisan_signed_at": None, "foreman_signed_by": None, "foreman_signed_at": None}


def _row(**o):
    base = {"id": 1, "work_order_number": "WO-00001", "status": "pending", "version": 3, "progress": 0,
            "artisan_sign": "", "foreman_sign": "", **LIFECYCLE}
    base.update(o)
    return base


async def _go(to, user=None, **body):
    return await transition_work_order(1, WorkOrderTransition(to=to, **body), user or _user())


# ─── the rules ──────────────────────────────────────────────────────────────────────

def test_a_user_may_start_complete_and_hold_but_not_hold_a_pending_job_or_reopen():
    assert [m.to for m in rules.allowed_moves("pending", "user")] == ["in-progress"]
    assert [m.to for m in rules.allowed_moves("in-progress", "user")] == ["completed", "on-hold"]
    assert rules.allowed_moves("completed", "user") == []
    assert [m.to for m in rules.allowed_moves("completed", "manager")] == ["in-progress"]
    assert [m.to for m in rules.allowed_moves("pending", "manager")] == ["in-progress", "on-hold"]


def test_a_viewer_may_move_nothing_and_an_unknown_status_has_no_moves():
    assert all(rules.allowed_moves(s, "viewer") == [] for s in rules.STATUSES)
    assert rules.allowed_moves("weird", "manager") == [] and rules.allowed_moves(None, "manager") == []


def test_permits_are_cleaned_and_unknown_keys_refused():
    out = rules.normalise_permits({"hot_work": {"required": 1, "reference": "  HW-9 ", "label": " Welding "}})
    assert out == {"hot_work": {"required": True, "reference": "HW-9", "label": "Welding"}}
    assert rules.normalise_permits(None) == {}
    with pytest.raises(ValueError):
        rules.normalise_permits({"free_beer": {}})
    with pytest.raises(ValueError):
        rules.normalise_permits({"hot_work": "yes"})
    with pytest.raises(ValueError):
        rules.normalise_permits([])


def test_missing_references_name_only_flagged_permits_without_one():
    permits = {"permit_to_work": {"required": True, "reference": "PTW-1"}, "hot_work": {"required": True, "reference": " "},
               "other": {"required": True, "reference": "", "label": "Crane lift plan"}, "confined_space": {"required": False}}
    assert rules.missing_permit_references(permits) == ["Hot work", "Crane lift plan"]
    assert rules.missing_permit_references(None) == [] and rules.missing_permit_references("x") == []


def test_only_an_image_data_url_counts_as_a_signature():
    assert rules.is_signature(SIG)
    assert not rules.is_signature("") and not rules.is_signature(None) and not rules.is_signature("data:image/png;base64,A")
    assert not rules.is_signature("J. Moyo")


# ─── transitions ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_records_the_first_start_and_an_event(patch_supabase):
    state = patch_supabase([_row()])
    out = await _go("in-progress")
    assert out["status"] == "in-progress" and out["started_at"]
    ev = state["maintenance_events"][0]
    assert (ev["action"], ev["from_status"], ev["to_status"]) == ("transition", "pending", "in-progress")


@pytest.mark.asyncio
async def test_restarting_keeps_the_first_start_time(patch_supabase):
    patch_supabase([_row(status="on-hold", started_at="2026-01-01T00:00:00")])
    out = await _go("in-progress")
    assert out["started_at"] == "2026-01-01T00:00:00"


@pytest.mark.asyncio
async def test_a_flagged_permit_without_a_reference_blocks_the_start_and_names_it(patch_supabase):
    state = patch_supabase([_row(permits={"hot_work": {"required": True, "reference": ""}})])
    with pytest.raises(HTTPException) as err:
        await _go("in-progress", user=_manager())
    assert err.value.status_code == 422 and err.value.detail["code"] == "permit_reference_missing"
    assert "Hot work" in err.value.detail["message"]
    assert state["work_orders"][0]["status"] == "pending" and not state.get("maintenance_events")


@pytest.mark.asyncio
async def test_a_reference_lets_the_job_start(patch_supabase):
    patch_supabase([_row(permits={"hot_work": {"required": True, "reference": "HW-9"}})])
    assert (await _go("in-progress"))["status"] == "in-progress"


@pytest.mark.asyncio
async def test_completing_needs_the_artisan_signature_and_stores_who_and_when(patch_supabase):
    state = patch_supabase([_row(status="in-progress")])
    with pytest.raises(HTTPException) as err:
        await _go("completed")
    assert err.value.status_code == 422 and err.value.detail["code"] == "signature_required"
    out = await _go("completed", artisan_sign=SIG)
    assert out["status"] == "completed" and out["progress"] == 100 and out["artisan_sign"] == SIG
    assert out["artisan_signed_by"] == "u@x.com" and out["artisan_signed_at"] and out["completed_at"]
    assert state["maintenance_events"][0]["signature"] == SIG


@pytest.mark.asyncio
async def test_holding_needs_a_reason_and_records_it(patch_supabase):
    state = patch_supabase([_row(status="in-progress")])
    with pytest.raises(HTTPException) as err:
        await _go("on-hold", reason="  ")
    assert err.value.status_code == 422 and err.value.detail["code"] == "reason_required"
    await _go("on-hold", reason="Waiting for the bearing")
    assert state["maintenance_events"][0]["note"] == "Waiting for the bearing"


@pytest.mark.asyncio
async def test_a_user_cannot_hold_a_pending_job_but_a_manager_can(patch_supabase):
    patch_supabase([_row()])
    with pytest.raises(HTTPException) as err:
        await _go("on-hold", reason="x")
    assert err.value.status_code == 403 and err.value.detail["code"] == "forbidden_role"
    assert (await _go("on-hold", user=_manager(), reason="x"))["status"] == "on-hold"


@pytest.mark.asyncio
async def test_a_move_the_table_does_not_have_is_409_and_lists_what_is_allowed(patch_supabase):
    patch_supabase([_row()])
    with pytest.raises(HTTPException) as err:
        await _go("completed", artisan_sign=SIG)
    assert err.value.status_code == 409 and err.value.detail["code"] == "transition_not_allowed"
    assert [m["to"] for m in err.value.detail["allowed"]] == ["in-progress"]


@pytest.mark.asyncio
async def test_moving_to_the_same_status_is_a_no_op_without_an_event(patch_supabase):
    state = patch_supabase([_row()])
    assert (await _go("pending"))["status"] == "pending"
    assert not state.get("maintenance_events")


@pytest.mark.asyncio
async def test_a_stale_version_is_a_conflict_and_nothing_changes(patch_supabase):
    state = patch_supabase([_row()])
    with pytest.raises(HTTPException) as err:
        await _go("in-progress", version=2)
    assert err.value.status_code == 409 and err.value.detail["code"] == "version_conflict"
    assert state["work_orders"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_reopening_a_completed_job_clears_the_foreman_sign_off_but_keeps_the_trail(patch_supabase):
    state = patch_supabase([_row(status="completed", foreman_sign=SIG, foreman_signed_by="m@x.com", foreman_signed_at="2026-01-02", completed_at="2026-01-01")])
    with pytest.raises(HTTPException):
        await _go("in-progress", reason="Leak found")  # a user may not reopen
    out = await _go("in-progress", user=_manager(), reason="Leak found")
    assert out["status"] == "in-progress" and out["foreman_sign"] == "" and out["foreman_signed_by"] is None and out["completed_at"] is None
    assert state["maintenance_events"][0]["note"] == "Leak found"


@pytest.mark.asyncio
async def test_moves_still_work_before_the_lifecycle_migration_is_applied(patch_supabase):
    legacy = {"id": 1, "work_order_number": "WO-00001", "status": "in-progress", "version": 3, "progress": 10, "artisan_sign": ""}
    patch_supabase([legacy])
    out = await _go("completed", artisan_sign=SIG)
    assert out["status"] == "completed" and "completed_at" not in out


@pytest.mark.asyncio
async def test_unknown_work_order_is_404(patch_supabase):
    patch_supabase([])
    with pytest.raises(HTTPException) as err:
        await _go("in-progress")
    assert err.value.status_code == 404


# ─── sign-off and the list of moves ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sign_off_records_who_and_when_and_keeps_the_status(patch_supabase):
    state = patch_supabase([_row(status="completed")])
    out = await sign_off_work_order(1, WorkOrderSignoff(foreman_sign=SIG), _manager())
    assert out["status"] == "completed" and out["foreman_sign"] == SIG and out["foreman_signed_by"] == "m@x.com" and out["foreman_signed_at"]
    ev = state["maintenance_events"][0]
    assert ev["action"] == "signed_off" and ev["signature"] == SIG


@pytest.mark.asyncio
async def test_sign_off_needs_a_completed_job_and_a_real_signature(patch_supabase):
    patch_supabase([_row(status="in-progress")])
    with pytest.raises(HTTPException) as err:
        await sign_off_work_order(1, WorkOrderSignoff(foreman_sign=SIG), _manager())
    assert err.value.status_code == 409
    patch_supabase([_row(status="completed")])
    with pytest.raises(HTTPException) as err2:
        await sign_off_work_order(1, WorkOrderSignoff(foreman_sign="typed name"), _manager())
    assert err2.value.status_code == 422 and err2.value.detail["code"] == "signature_required"


@pytest.mark.asyncio
async def test_transitions_endpoint_lists_only_the_moves_open_to_the_caller(patch_supabase):
    patch_supabase([_row(status="in-progress")])
    out = await get_work_order_transitions(1, _user())
    assert [(m["to"], m["needs_signature"], m["needs_reason"]) for m in out] == [("completed", True, False), ("on-hold", False, True)]
    assert await get_work_order_transitions(1, {"role": "viewer"}) == []


# ─── permits through the existing edit, and shadow mode ─────────────────────────────

@pytest.mark.asyncio
async def test_permits_can_be_saved_through_the_edit_and_are_cleaned(patch_supabase):
    patch_supabase([_row()])
    out = await update_work_order(1, WorkOrderUpdate(permits={"hot_work": {"required": True, "reference": " HW-1 "}}), _user())
    assert out["permits"] == {"hot_work": {"required": True, "reference": "HW-1"}}


def test_an_unknown_permit_is_refused_by_the_model():
    with pytest.raises(ValueError):
        WorkOrderUpdate(permits={"nope": {"required": True}})


@pytest.mark.asyncio
async def test_a_status_edit_the_rules_refuse_still_happens_but_is_logged(patch_supabase, caplog):
    state = patch_supabase([_row(status="completed")])
    with caplog.at_level("WARNING"):
        out = await update_work_order(1, WorkOrderUpdate(status="pending"), _user())
    assert out["status"] == "pending" and state["work_orders"][0]["status"] == "pending"
    assert "shadow_refusal" in caplog.text
