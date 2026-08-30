# tests/test_compressors_crud.py — the compressor CRUD route handlers
# (get_compressors, get_compressor_by_id, create_compressor, update_compressor,
# update_compressor_status, delete_compressor). compressors.py was 32% covered
# with only its pure calc helpers and get_performance_metrics tested; none of
# these route bodies had any coverage. Uses the shared bespoke fake
# (tests/_compressors_fake.py) since these routes filter/insert/update against
# a real table, not a canned static payload.

import pytest
from fastapi import HTTPException

from app.routers.compressors import (
    get_compressors, get_compressor_by_id, create_compressor, update_compressor,
    update_compressor_status, delete_compressor,
    CompressorCreate, CompressorUpdate, StatusUpdateRequest, CompressorStatus,
    COMPRESSORS_TABLE,
)
from tests._compressors_fake import FakeSupabase


def _compressor(**overrides):
    base = {
        "id": "c1", "name": "Compressor A", "model": "X1", "capacity": "500cfm",
        "status": "running", "location": "Plant 1", "color": "bg-blue-500",
        "initial_total_running": 0.0, "initial_total_loaded": 0.0,
        "total_running_hours": 100.0, "total_loaded_hours": 80.0,
        "created_at": "2024-01-01T00:00:00",
    }
    base.update(overrides)
    return base


# ─── get_compressors ─────────────────────────────────────────────────────────────────

async def test_get_compressors_adds_efficiency_to_each_row():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(total_running_hours=10, total_loaded_hours=8)]})
    result = await get_compressors(status=None, location=None, supabase_client=fake)
    assert len(result) == 1
    assert result[0]["efficiency"] == 80.0


async def test_get_compressors_empty_table_is_empty_list():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    result = await get_compressors(status=None, location=None, supabase_client=fake)
    assert result == []


async def test_get_compressors_filters_by_status():
    fake = FakeSupabase({COMPRESSORS_TABLE: [
        _compressor(id="c1", status="running"),
        _compressor(id="c2", status="offline"),
    ]})
    result = await get_compressors(status="offline", location=None, supabase_client=fake)
    assert [c["id"] for c in result] == ["c2"]


async def test_get_compressors_filters_by_location():
    fake = FakeSupabase({COMPRESSORS_TABLE: [
        _compressor(id="c1", location="Plant 1"),
        _compressor(id="c2", location="Plant 2"),
    ]})
    result = await get_compressors(status=None, location="Plant 2", supabase_client=fake)
    assert [c["id"] for c in result] == ["c2"]


async def test_get_compressors_db_failure_raises_500_not_a_fake_empty_list():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "connection refused")
    with pytest.raises(HTTPException) as exc:
        await get_compressors(status=None, location=None, supabase_client=fake)
    assert exc.value.status_code == 500


# ─── get_compressor_by_id ────────────────────────────────────────────────────────────

async def test_get_compressor_by_id_found_includes_efficiency():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(id="c1", total_running_hours=10, total_loaded_hours=5)]})
    result = await get_compressor_by_id(compressor_id="c1", supabase_client=fake)
    assert result["id"] == "c1"
    assert result["efficiency"] == 50.0


async def test_get_compressor_by_id_not_found_is_404():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    with pytest.raises(HTTPException) as exc:
        await get_compressor_by_id(compressor_id="ghost", supabase_client=fake)
    assert exc.value.status_code == 404


async def test_get_compressor_by_id_db_failure_is_500_and_preserves_404_semantics():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await get_compressor_by_id(compressor_id="c1", supabase_client=fake)
    assert exc.value.status_code == 500


# ─── create_compressor ───────────────────────────────────────────────────────────────

async def test_create_compressor_inserts_and_returns_stored_row():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    payload = CompressorCreate(name="New", model="M1", capacity="300cfm", location="Plant 3")
    result = await create_compressor(compressor=payload, supabase_client=fake, current_user={"user_id": "u1"})
    assert result["success"] is True
    assert result["data"]["name"] == "New"
    assert "id" in result["data"]
    assert len(fake.state.tables[COMPRESSORS_TABLE]) == 1


async def test_create_compressor_mock_branch_when_insert_returns_no_data():
    class _NoDataFake:
        def table(self, name):
            class _Q:
                def insert(self, data): self._data = data; return self
                def execute(self): return type("R", (), {"data": None})()
            return _Q()

    payload = CompressorCreate(name="New", model="M1", capacity="300cfm", location="Plant 3")
    result = await create_compressor(compressor=payload, supabase_client=_NoDataFake(), current_user={})
    assert result["success"] is True
    assert "mock" in result["message"]
    assert result["data"]["name"] == "New"


async def test_create_compressor_db_failure_is_500():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "insert boom")
    payload = CompressorCreate(name="New", model="M1", capacity="300cfm", location="Plant 3")
    with pytest.raises(HTTPException) as exc:
        await create_compressor(compressor=payload, supabase_client=fake, current_user={})
    assert exc.value.status_code == 500


# ─── update_compressor ───────────────────────────────────────────────────────────────

async def test_update_compressor_partial_update_only_sends_set_fields():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(id="c1", name="Old Name", location="Plant 1")]})
    update = CompressorUpdate(name="Renamed")
    result = await update_compressor(compressor_id="c1", compressor_update=update, supabase_client=fake, current_user={})
    assert result["success"] is True
    assert result["data"]["name"] == "Renamed"
    # untouched field survives the partial update
    assert result["data"]["location"] == "Plant 1"


async def test_update_compressor_mock_branch_when_no_matching_row():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    update = CompressorUpdate(name="Renamed")
    result = await update_compressor(compressor_id="ghost", compressor_update=update, supabase_client=fake, current_user={})
    assert result == {"success": True, "message": "Compressor updated successfully"}


async def test_update_compressor_db_failure_is_500():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "update boom")
    update = CompressorUpdate(name="Renamed")
    with pytest.raises(HTTPException) as exc:
        await update_compressor(compressor_id="c1", compressor_update=update, supabase_client=fake, current_user={})
    assert exc.value.status_code == 500


# ─── update_compressor_status ────────────────────────────────────────────────────────

async def test_update_compressor_status_happy_path():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(id="c1", status="standby")]})
    result = await update_compressor_status(
        compressor_id="c1", status_update=StatusUpdateRequest(status=CompressorStatus.RUNNING),
        supabase_client=fake, current_user={})
    assert result["success"] is True
    assert result["data"]["status"] == "running"
    assert "running" in result["message"]


async def test_update_compressor_status_mock_branch_when_no_matching_row():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    result = await update_compressor_status(
        compressor_id="ghost", status_update=StatusUpdateRequest(status=CompressorStatus.OFFLINE),
        supabase_client=fake, current_user={})
    assert result == {"success": True, "message": "Compressor status updated"}


async def test_update_compressor_status_db_failure_is_500():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "status boom")
    with pytest.raises(HTTPException) as exc:
        await update_compressor_status(
            compressor_id="c1", status_update=StatusUpdateRequest(status=CompressorStatus.OFFLINE),
            supabase_client=fake, current_user={})
    assert exc.value.status_code == 500


# ─── delete_compressor ───────────────────────────────────────────────────────────────

async def test_delete_compressor_removes_the_row():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(id="c1"), _compressor(id="c2")]})
    result = await delete_compressor(compressor_id="c1", supabase_client=fake, current_user={})
    assert result == {"success": True, "message": "Compressor deleted successfully"}
    remaining_ids = [c["id"] for c in fake.state.tables[COMPRESSORS_TABLE]]
    assert remaining_ids == ["c2"]


async def test_delete_compressor_nonexistent_id_is_still_a_no_op_success():
    # delete() on a Supabase table doesn't error on "0 rows matched" — the route
    # doesn't check, so deleting an already-gone id is reported as success.
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(id="c1")]})
    result = await delete_compressor(compressor_id="ghost", supabase_client=fake, current_user={})
    assert result["success"] is True
    assert len(fake.state.tables[COMPRESSORS_TABLE]) == 1


async def test_delete_compressor_db_failure_is_500():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "delete boom")
    with pytest.raises(HTTPException) as exc:
        await delete_compressor(compressor_id="c1", supabase_client=fake, current_user={})
    assert exc.value.status_code == 500
