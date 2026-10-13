# tests/test_compressor_register.py — the compressors come from the Equipment register: which exist, what they are
# called, and where they are. Covers the register module itself and how the compressors routes use it.

import pytest
from fastapi import HTTPException

from app.compressor_register import fields_from_register, load_register_units, sync_from_register, with_register
from app.routers.compressors import (
    COMPRESSORS_TABLE, CompressorUpdate, delete_compressor, get_compressor_by_id, get_compressors, update_compressor,
)
from tests._compressors_fake import FakeSupabase


def _unit(id, code, name, location="Southwell ", model=None, category="Compressors"):
    return {"id": id, "equipment_id": code, "name": name, "location": location, "model": model, "category": category, "serial_number": None}


def _row(id, equipment_id=None, **over):
    base = {"id": id, "name": "Old", "model": "", "capacity": "", "status": "running", "location": "Old place", "color": "bg-blue-500",
            "equipment_id": equipment_id, "total_running_hours": 10.0, "total_loaded_hours": 5.0, "created_at": "2026-01-01T00:00:00"}
    base.update(over)
    return base


def test_fields_from_register_trims_and_never_leaves_a_unit_unnamed():
    assert fields_from_register(_unit(1, "CMP 01", " Compressor No 1 ", " Southwell ")) == {"name": "Compressor No 1", "model": "", "location": "Southwell"}
    assert fields_from_register({"id": 7, "equipment_id": "CMP 07", "name": None})["name"] == "CMP 07"
    assert fields_from_register({"id": 8, "equipment_id": None, "name": ""})["name"] == "Equipment 8"


def test_only_compressor_categories_are_read_from_the_register():
    fake = FakeSupabase({"equipment": [_unit(1, "CMP 01", "No 1"), _unit(2, "TRK 01", "Truck", category="Trucks"), _unit(3, "CMP 02", "No 2", category="compressor")]})
    assert [u["id"] for u in load_register_units(fake)] == [1, 3]


def test_a_register_that_cannot_be_read_gives_no_units_rather_than_failing():
    fake = FakeSupabase({})
    fake.always_fail("equipment", "boom")
    assert load_register_units(fake) == []


def test_sync_creates_a_meter_row_for_each_register_compressor_that_has_none():
    fake = FakeSupabase({"equipment": [_unit(1, "CMP 01", "No 1"), _unit(2, "CMP 02", "No 2")], COMPRESSORS_TABLE: [_row("c1", equipment_id=1, name="No 1", location="Southwell")]})
    result = sync_from_register(fake)
    assert result == {"created": 1, "updated": 0}
    rows = fake.state.tables[COMPRESSORS_TABLE]
    assert len(rows) == 2 and rows[1]["equipment_id"] == 2 and rows[1]["status"] == "standby" and rows[1]["name"] == "No 2"


def test_sync_refreshes_a_stored_copy_that_drifted_from_the_register_and_nothing_else():
    fake = FakeSupabase({"equipment": [_unit(1, "CMP 01", "Renamed", "New shaft")], COMPRESSORS_TABLE: [_row("c1", equipment_id=1, name="Old", location="Old place", status="maintenance")]})
    assert sync_from_register(fake) == {"created": 0, "updated": 1}
    row = fake.state.tables[COMPRESSORS_TABLE][0]
    assert row["name"] == "Renamed" and row["location"] == "New shaft"
    assert row["status"] == "maintenance" and row["total_running_hours"] == 10.0  # the meters and the state are the compressor's own


def test_sync_is_idempotent_and_never_raises():
    fake = FakeSupabase({"equipment": [_unit(1, "CMP 01", "No 1", "Southwell")], COMPRESSORS_TABLE: []})
    assert sync_from_register(fake)["created"] == 1
    assert sync_from_register(fake) == {"created": 0, "updated": 0}
    broken = FakeSupabase({"equipment": [_unit(1, "CMP 01", "No 1")]})
    broken.always_fail(COMPRESSORS_TABLE, "boom")
    assert sync_from_register(broken) == {"created": 0, "updated": 0}  # logged, not raised


def test_with_register_adds_the_equipment_code_and_marks_unlinked_rows():
    rows = with_register([_row("c1", equipment_id=1), _row("c2")], [_unit(1, "CMP 01", "No 1")])
    assert rows[0]["equipment_code"] == "CMP 01" and rows[0]["name"] == "No 1"
    assert rows[1]["equipment_code"] is None and rows[1]["name"] == "Old"


async def test_the_list_is_the_registers_compressors_with_their_register_details():
    fake = FakeSupabase({"equipment": [_unit(1, "CMP 01", "No 1", "Southwell"), _unit(2, "CMP 02", "No 2", "4L Lobe Shaft")], COMPRESSORS_TABLE: []})
    rows = await get_compressors(status=None, location=None, supabase_client=fake)
    assert sorted(r["equipment_code"] for r in rows) == ["CMP 01", "CMP 02"]
    assert {r["location"] for r in rows} == {"Southwell", "4L Lobe Shaft"}
    again = await get_compressors(status=None, location=None, supabase_client=fake)
    assert len(again) == 2 and len(fake.state.tables[COMPRESSORS_TABLE]) == 2  # reading twice does not create twice


async def test_the_location_filter_uses_the_registers_location():
    fake = FakeSupabase({"equipment": [_unit(1, "CMP 01", "No 1", "Southwell"), _unit(2, "CMP 02", "No 2", "10.5L")], COMPRESSORS_TABLE: []})
    rows = await get_compressors(status=None, location="10.5L", supabase_client=fake)
    assert [r["equipment_code"] for r in rows] == ["CMP 02"]


async def test_a_register_outage_leaves_the_stored_list_available():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_row("c1", equipment_id=1, name="Stored name")]})
    fake.always_fail("equipment", "boom")
    rows = await get_compressors(status=None, location=None, supabase_client=fake)
    assert [r["name"] for r in rows] == ["Stored name"]


async def test_one_compressor_carries_its_register_details():
    fake = FakeSupabase({"equipment": [_unit(1, "CMP 01", "Register name")], COMPRESSORS_TABLE: [_row("c1", equipment_id=1)]})
    row = await get_compressor_by_id(compressor_id="c1", supabase_client=fake)
    assert row["name"] == "Register name" and row["equipment_code"] == "CMP 01"


async def test_a_register_compressors_name_and_location_cannot_be_changed_here():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_row("c1", equipment_id=1, name="No 1", location="Southwell")]})
    result = await update_compressor(compressor_id="c1", compressor_update=CompressorUpdate(name="Mine", location="Elsewhere", notes="x"), supabase_client=fake, current_user={})
    assert sorted(result["ignored_fields"]) == ["location", "name"]
    row = fake.state.tables[COMPRESSORS_TABLE][0]
    assert row["name"] == "No 1" and row["location"] == "Southwell" and row["notes"] == "x"
    assert "Equipment register" in result["message"]


async def test_an_unlinked_compressor_can_still_be_edited_and_deleted():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_row("c1")]})
    result = await update_compressor(compressor_id="c1", compressor_update=CompressorUpdate(name="Mine"), supabase_client=fake, current_user={})
    assert result["ignored_fields"] == [] and fake.state.tables[COMPRESSORS_TABLE][0]["name"] == "Mine"
    await delete_compressor(compressor_id="c1", supabase_client=fake, current_user={})
    assert fake.state.tables[COMPRESSORS_TABLE] == []


async def test_a_register_compressor_cannot_be_deleted_here_because_it_would_reappear():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_row("c1", equipment_id=1)]})
    with pytest.raises(HTTPException) as exc:
        await delete_compressor(compressor_id="c1", supabase_client=fake, current_user={})
    assert exc.value.status_code == 409
    assert len(fake.state.tables[COMPRESSORS_TABLE]) == 1
