# tests/test_breakdowns_create.py — POST /api/breakdowns/: verifies the route
# actually WIRES UP calculate_time_metrics/calculate_spare_costs (already unit-tested
# in isolation in test_breakdowns_time_metrics.py) into the insert payload and the
# returned record, the spares_used JSON encode/decode round-trip, the
# learn_lookup_value best-effort side effect on location/breakdown_nature (including
# that it must never fail the parent create), and the insert-returns-nothing -> 500
# and generic-exception -> 500 paths. Zero prior tests for this endpoint. Uses the
# sanctioned "call the route coroutine directly against a fake supabase client"
# recipe (fake defined in tests/_breakdowns_fake.py).

import pytest
from fastapi import HTTPException

import app.routers.breakdowns as bd
from app.routers.breakdowns import create_breakdown, BreakdownCreate, SparePart
from tests._breakdowns_fake import FakeSupabase

CURRENT_USER = {"user_id": "u1", "email": "t@t", "role": "user"}


def _make(**overrides) -> BreakdownCreate:
    defaults = dict(
        machine_id="M-1", machine_name="Crusher 1", artisan_name="T. Moyo",
        department="Milling", location="Bay 3", breakdown_date="2026-08-30",
        breakdown_type="Mechanical",
    )
    defaults.update(overrides)
    return BreakdownCreate(**defaults)


def _patch(monkeypatch, tables=None):
    fake = FakeSupabase(tables or {"breakdowns": [], "lookup_lists": []})
    monkeypatch.setattr(bd, "supabase", fake)
    return fake


# ─── Happy path: no spares ──────────────────────────────────────────────────────────

async def test_create_minimal_breakdown_returns_success_shape(monkeypatch):
    _patch(monkeypatch)
    result = await create_breakdown(_make(), current_user=CURRENT_USER)
    assert result["success"] is True
    assert result["message"] == "Breakdown created successfully"
    assert result["data"]["machine_id"] == "M-1"
    assert result["data"]["spares_used"] == []
    assert result["data"]["total_spare_cost"] == 0.0


async def test_create_sets_created_and_updated_timestamps(monkeypatch):
    fake = _patch(monkeypatch)
    await create_breakdown(_make(), current_user=CURRENT_USER)
    payload = next(c["payload"] for c in fake.state.calls if c["table"] == "breakdowns" and c["op"] == "insert")
    assert "created_at" in payload and "updated_at" in payload
    assert payload["created_at"] == payload["updated_at"]


# ─── BreakdownCreate validators ─────────────────────────────────────────────────────

async def test_create_blank_breakdown_date_defaults_to_today(monkeypatch):
    from datetime import datetime
    _patch(monkeypatch)
    result = await create_breakdown(_make(breakdown_date=""), current_user=CURRENT_USER)
    assert result["data"]["breakdown_date"] == datetime.utcnow().strftime('%Y-%m-%d')


async def test_create_explicit_machine_description_is_kept_verbatim(monkeypatch):
    _patch(monkeypatch)
    result = await create_breakdown(
        _make(machine_description="Explicit machine note", breakdown_description="Different narrative"),
        current_user=CURRENT_USER,
    )
    # machine_description was explicitly provided, so populate_machine_description's
    # fallback-from-breakdown_description branch must NOT overwrite it.
    assert result["data"]["machine_description"] == "Explicit machine note"


# ─── Time metrics actually invoked from the route ───────────────────────────────────

async def test_create_computes_time_metrics_from_time_fields(monkeypatch):
    _patch(monkeypatch)
    breakdown = _make(
        breakdown_start="08:00", breakdown_end="10:00",
        work_start="08:30", work_end="09:30",
    )
    result = await create_breakdown(breakdown, current_user=CURRENT_USER)
    data = result["data"]
    assert data["response_time_minutes"] == 30
    assert data["repair_time_minutes"] == 60
    assert data["downtime_minutes"] == 120
    assert data["net_downtime_minutes"] == 120


# ─── Spare costs actually invoked from the route ────────────────────────────────────

async def test_create_computes_spare_costs_and_persists_as_json(monkeypatch):
    fake = _patch(monkeypatch)
    breakdown = _make(spares_used=[
        SparePart(name="Bearing", quantity=2, unit_price=15.0),
        SparePart(name="Seal", quantity=1, unit_price=8.5),
    ])
    result = await create_breakdown(breakdown, current_user=CURRENT_USER)
    assert result["data"]["total_spare_cost"] == 38.5
    assert result["data"]["spares_used"][0]["total_cost"] == 30.0
    assert result["data"]["spares_used"][1]["total_cost"] == 8.5

    insert_call = next(c for c in fake.state.calls if c["table"] == "breakdowns" and c["op"] == "insert")
    # persisted through encode_json_fields as a JSON string, not a raw list
    assert isinstance(insert_call["payload"]["spares_used"], str)
    assert '"total_cost": 30.0' in insert_call["payload"]["spares_used"]


# ─── learn_lookup_value side effect ─────────────────────────────────────────────────

async def test_create_learns_new_location_and_breakdown_nature(monkeypatch):
    fake = _patch(monkeypatch, tables={"breakdowns": [], "lookup_lists": []})
    await create_breakdown(_make(location="Bay 9", breakdown_nature="Bearing Failure"), current_user=CURRENT_USER)

    lookup_inserts = [c for c in fake.state.calls if c["table"] == "lookup_lists" and c["op"] == "insert"]
    inserted_values = {(c["payload"]["list_name"], c["payload"]["value"]) for c in lookup_inserts}
    assert ("location", "Bay 9") in inserted_values
    assert ("breakdown_nature", "Bearing Failure") in inserted_values


async def test_create_does_not_duplicate_an_existing_lookup_value(monkeypatch):
    fake = _patch(monkeypatch, tables={
        "breakdowns": [],
        "lookup_lists": [{"id": "l1", "list_name": "location", "value": "Bay 3"}],
    })
    # Same value, different case — learn_lookup_value's ilike check must still match it.
    await create_breakdown(_make(location="bay 3"), current_user=CURRENT_USER)

    lookup_inserts = [c for c in fake.state.calls if c["table"] == "lookup_lists" and c["op"] == "insert"]
    assert all(c["payload"]["value"] != "bay 3" for c in lookup_inserts)


async def test_create_succeeds_even_if_learn_lookup_value_blows_up(monkeypatch):
    # learn_lookup_value is documented as best-effort and must never fail the parent
    # create — force every lookup_lists call to raise and confirm the breakdown still
    # saves successfully.
    fake = _patch(monkeypatch, tables={"breakdowns": [], "lookup_lists": []})
    fake.always_fail("lookup_lists", "lookup table is down")
    result = await create_breakdown(_make(location="Bay 9", breakdown_nature="X"), current_user=CURRENT_USER)
    assert result["success"] is True


# ─── Failure paths ───────────────────────────────────────────────────────────────────

async def test_create_returns_500_when_insert_returns_no_row(monkeypatch):
    fake = _patch(monkeypatch)
    fake.state.insert_returns_empty.add("breakdowns")
    with pytest.raises(HTTPException) as exc:
        await create_breakdown(_make(), current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    assert "Failed to create breakdown" in exc.value.detail


async def test_create_returns_500_on_db_exception(monkeypatch):
    fake = _patch(monkeypatch)
    fake.always_fail("breakdowns", "db exploded")
    with pytest.raises(HTTPException) as exc:
        await create_breakdown(_make(), current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    assert "db exploded" in exc.value.detail


async def test_create_no_supabase_client_raises_500(monkeypatch):
    monkeypatch.setattr(bd, "supabase", None)
    with pytest.raises(HTTPException) as exc:
        await create_breakdown(_make(), current_user=CURRENT_USER)
    assert exc.value.status_code == 500
