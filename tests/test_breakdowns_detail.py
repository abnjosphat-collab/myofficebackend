# tests/test_breakdowns_detail.py — GET/PATCH/DELETE /api/breakdowns/{id}: get_breakdown
# (+404), update_breakdown's exclude_unset partial-update semantics (including the
# null-vs-unset distinction this codebase has previously regressed on — see
# myoffice CLAUDE.md's PATCH null-filter history), its breakdown_description ->
# machine_description mapping, re-running calculate_time_metrics/calculate_spare_costs
# only when the relevant fields are touched, learn_lookup_value on update, and
# delete_breakdown. Zero prior tests for any of these three endpoints. Uses the
# sanctioned "call the route coroutine directly against a fake supabase client" recipe
# (fake defined in tests/_breakdowns_fake.py).

import pytest
from fastapi import HTTPException

import app.routers.breakdowns as bd
from app.routers.breakdowns import get_breakdown, update_breakdown, delete_breakdown, BreakdownUpdate
from tests._breakdowns_fake import FakeSupabase

CURRENT_USER = {"user_id": "u1", "email": "t@t", "role": "manager"}

EXISTING = {
    "id": "b1", "machine_id": "M-1", "machine_name": "Crusher 1", "artisan_name": "T. Moyo",
    "department": "Milling", "location": "Bay 3", "breakdown_date": "2026-08-30",
    "breakdown_type": "Mechanical", "breakdown_nature": "Bearing Failure",
    "status": "logged", "priority": "medium",
    "breakdown_start": "08:00", "breakdown_end": "10:00",
    "work_start": "08:30", "work_end": "09:30",
    "response_time_minutes": 30, "repair_time_minutes": 60,
    "downtime_minutes": 120, "net_downtime_minutes": 120,
    "spares_used": "[]", "total_spare_cost": 0.0,
    "machine_description": "", "breakdown_description": "",
    "work_done": None, "artisan_recommendations": None,
}


def _patch(monkeypatch, breakdowns=None, lookup_lists=None):
    fake = FakeSupabase({
        "breakdowns": breakdowns if breakdowns is not None else [dict(EXISTING)],
        "lookup_lists": lookup_lists if lookup_lists is not None else [],
    })
    monkeypatch.setattr(bd, "supabase", fake)
    return fake


# ─── get_breakdown ──────────────────────────────────────────────────────────────────

async def test_get_breakdown_returns_decoded_record(monkeypatch):
    _patch(monkeypatch)
    result = await get_breakdown("b1")
    assert result["id"] == "b1"
    assert result["spares_used"] == []


async def test_get_breakdown_404_when_missing(monkeypatch):
    _patch(monkeypatch, breakdowns=[])
    with pytest.raises(HTTPException) as exc:
        await get_breakdown("missing")
    assert exc.value.status_code == 404


async def test_get_breakdown_500_on_db_exception(monkeypatch):
    fake = _patch(monkeypatch)
    fake.always_fail("breakdowns", "db exploded")
    with pytest.raises(HTTPException) as exc:
        await get_breakdown("b1")
    assert exc.value.status_code == 500


# ─── update_breakdown ───────────────────────────────────────────────────────────────

async def test_update_only_sends_explicitly_provided_fields(monkeypatch):
    fake = _patch(monkeypatch)
    await update_breakdown("b1", BreakdownUpdate(status="in_progress"), current_user=CURRENT_USER)
    update_call = next(c for c in fake.state.calls if c["table"] == "breakdowns" and c["op"] == "update")
    assert update_call["payload"]["status"] == "in_progress"
    # untouched fields must not be present in the update payload at all
    assert "priority" not in update_call["payload"]
    assert "machine_name" not in update_call["payload"]


async def test_update_explicit_null_clears_a_field_unset_field_is_untouched(monkeypatch):
    # The null-vs-unset PATCH bug this codebase has hit before: an explicitly-passed
    # None must reach the update payload (a real "clear this field"), while a field
    # never mentioned in the request must be excluded entirely, not silently nulled.
    fake = _patch(monkeypatch)
    update = BreakdownUpdate(work_done=None)  # explicitly set to None
    await update_breakdown("b1", update, current_user=CURRENT_USER)
    update_call = next(c for c in fake.state.calls if c["table"] == "breakdowns" and c["op"] == "update")
    assert "work_done" in update_call["payload"]
    assert update_call["payload"]["work_done"] is None
    assert "artisan_recommendations" not in update_call["payload"]  # never mentioned


async def test_update_maps_breakdown_description_to_machine_description(monkeypatch):
    fake = _patch(monkeypatch)
    await update_breakdown("b1", BreakdownUpdate(breakdown_description="New narrative"), current_user=CURRENT_USER)
    update_call = next(c for c in fake.state.calls if c["table"] == "breakdowns" and c["op"] == "update")
    assert update_call["payload"]["machine_description"] == "New narrative"


async def test_update_recomputes_spare_costs(monkeypatch):
    fake = _patch(monkeypatch)
    result = await update_breakdown(
        "b1",
        BreakdownUpdate(spares_used=[{"name": "Bearing", "quantity": 2, "unit_price": 15.0}]),
        current_user=CURRENT_USER,
    )
    assert result["total_spare_cost"] == 30.0
    update_call = next(c for c in fake.state.calls if c["table"] == "breakdowns" and c["op"] == "update")
    assert update_call["payload"]["total_spare_cost"] == 30.0
    assert isinstance(update_call["payload"]["spares_used"], str)  # JSON-encoded for storage


async def test_update_empty_spares_used_zeroes_cost(monkeypatch):
    fake = _patch(monkeypatch, breakdowns=[{**EXISTING, "spares_used": '[{"name": "Old"}]', "total_spare_cost": 12.0}])
    result = await update_breakdown("b1", BreakdownUpdate(spares_used=[]), current_user=CURRENT_USER)
    assert result["total_spare_cost"] == 0.0


async def test_update_recomputes_time_metrics_when_a_time_field_changes(monkeypatch):
    # Existing record's work_end is 09:30 (60 min repair); changing only work_start
    # to 08:00 should recompute using the EXISTING work_end merged in, not zero it out.
    fake = _patch(monkeypatch)
    result = await update_breakdown("b1", BreakdownUpdate(work_start="08:00"), current_user=CURRENT_USER)
    assert result["repair_time_minutes"] == 90  # 09:30 - 08:00, using existing work_end
    update_call = next(c for c in fake.state.calls if c["table"] == "breakdowns" and c["op"] == "update")
    assert update_call["payload"]["repair_time_minutes"] == 90


async def test_update_does_not_touch_time_metrics_when_no_time_field_changes(monkeypatch):
    fake = _patch(monkeypatch)
    await update_breakdown("b1", BreakdownUpdate(status="resolved"), current_user=CURRENT_USER)
    update_call = next(c for c in fake.state.calls if c["table"] == "breakdowns" and c["op"] == "update")
    assert "repair_time_minutes" not in update_call["payload"]
    assert "downtime_minutes" not in update_call["payload"]


async def test_update_learns_new_location_and_breakdown_nature(monkeypatch):
    fake = _patch(monkeypatch, lookup_lists=[])
    await update_breakdown(
        "b1", BreakdownUpdate(location="New Bay", breakdown_nature="New Fault"), current_user=CURRENT_USER,
    )
    lookup_inserts = [c for c in fake.state.calls if c["table"] == "lookup_lists" and c["op"] == "insert"]
    inserted = {(c["payload"]["list_name"], c["payload"]["value"]) for c in lookup_inserts}
    assert ("location", "New Bay") in inserted
    assert ("breakdown_nature", "New Fault") in inserted


async def test_update_404_when_missing(monkeypatch):
    _patch(monkeypatch, breakdowns=[])
    with pytest.raises(HTTPException) as exc:
        await update_breakdown("missing", BreakdownUpdate(status="resolved"), current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_update_500_when_update_returns_no_row(monkeypatch):
    fake = _patch(monkeypatch)
    fake.state.update_returns_empty.add("breakdowns")
    with pytest.raises(HTTPException) as exc:
        await update_breakdown("b1", BreakdownUpdate(status="resolved"), current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    assert "Update failed" in exc.value.detail


async def test_update_500_on_db_exception(monkeypatch):
    fake = _patch(monkeypatch)
    fake.always_fail("breakdowns", "db exploded")
    with pytest.raises(HTTPException) as exc:
        await update_breakdown("b1", BreakdownUpdate(status="resolved"), current_user=CURRENT_USER)
    assert exc.value.status_code == 500


# ─── delete_breakdown ────────────────────────────────────────────────────────────────

async def test_delete_removes_the_record(monkeypatch):
    fake = _patch(monkeypatch)
    result = await delete_breakdown("b1", current_user=CURRENT_USER)
    assert result == {"success": True, "message": "Breakdown deleted successfully"}
    delete_call = next(c for c in fake.state.calls if c["table"] == "breakdowns" and c["op"] == "delete")
    assert delete_call["eq"] == {"id": "b1"}
    assert fake.state.tables["breakdowns"] == []


async def test_delete_404_when_missing(monkeypatch):
    _patch(monkeypatch, breakdowns=[])
    with pytest.raises(HTTPException) as exc:
        await delete_breakdown("missing", current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_delete_500_on_db_exception(monkeypatch):
    fake = _patch(monkeypatch)
    fake.always_fail("breakdowns", "db exploded")
    with pytest.raises(HTTPException) as exc:
        await delete_breakdown("b1", current_user=CURRENT_USER)
    assert exc.value.status_code == 500
