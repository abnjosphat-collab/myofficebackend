# tests/test_compressors_export_import.py — export_data (CSV StreamingResponse),
# import_data (retired: compressors come from the Equipment register), and create_service_record. All zero
# coverage before this file.
#
# import_data is decorated with @limiter.limit(...) (slowapi). Calling the
# route coroutine directly (this session's sanctioned recipe) would go through
# slowapi's wrapper, which expects a real Request wired to a rate-limiter
# state that isn't worth faking just to reach the underlying CSV-parsing
# logic under test here. slowapi decorators preserve the original function on
# `.__wrapped__` (confirmed: `import_data.__wrapped__ is` the pre-decoration
# coroutine), so tests call that directly instead — same technique, just
# skipping a decorator that has nothing to do with what's being tested.

import pytest
from fastapi import HTTPException

from app.routers.compressors import (
    export_data, import_data, create_service_record,
    ServiceRecordCreate, COMPRESSORS_TABLE, SERVICE_RECORDS_TABLE,
)
from tests._compressors_fake import FakeSupabase

_import_data = import_data.__wrapped__  # bypass the slowapi rate-limit decorator


class _FakeUploadFile:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


def _compressor(**overrides):
    base = {"id": "c1", "name": "Compressor A", "model": "X1", "capacity": "500cfm",
            "status": "running", "location": "Plant 1",
            "total_running_hours": 100.0, "total_loaded_hours": 80.0}
    base.update(overrides)
    return base


# ─── export_data ──────────────────────────────────────────────────────────────────────

async def _read_streaming_body(response) -> str:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    return "".join(chunks)


async def test_export_produces_header_and_a_row_per_compressor():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(name="A", total_running_hours=100.0, total_loaded_hours=80.0)]})
    response = await export_data(supabase_client=fake, current_user={})
    body = await _read_streaming_body(response)
    lines = body.strip().split("\n")
    assert lines[0] == "Name,Model,Capacity,Status,Location,Total Running Hours,Total Loaded Hours,Efficiency"
    assert lines[1] == "A,X1,500cfm,running,Plant 1,100.0,80.0,80.0"
    assert response.media_type == "text/csv"
    assert response.headers["Content-Disposition"] == "attachment; filename=compressor_export.csv"


async def test_export_empty_fleet_is_header_only():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    response = await export_data(supabase_client=fake, current_user={})
    body = await _read_streaming_body(response)
    assert body.strip() == "Name,Model,Capacity,Status,Location,Total Running Hours,Total Loaded Hours,Efficiency"


async def test_export_db_failure_is_500():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "boom")
    with pytest.raises(HTTPException) as exc:
        await export_data(supabase_client=fake, current_user={})
    assert exc.value.status_code == 500


# ─── import_data (retired) ────────────────────────────────────────────────────────────

async def test_import_is_retired_and_points_to_the_equipment_register():
    """Compressors come from the Equipment register, so a file can no longer create them (and nothing is written)."""
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    file = _FakeUploadFile("import.csv", b"name,model,capacity,status,location,running,loaded\nComp A,X1,500cfm,running,Plant 1,120,90\n")
    with pytest.raises(HTTPException) as exc:
        await _import_data(request=None, file=file, supabase_client=fake, current_user={})
    assert exc.value.status_code == 410
    assert "Equipment register" in exc.value.detail
    assert fake.state.tables[COMPRESSORS_TABLE] == []


# ─── create_service_record ───────────────────────────────────────────────────────────

async def test_create_service_record_inserts_and_returns_data():
    fake = FakeSupabase({SERVICE_RECORDS_TABLE: []})
    payload = ServiceRecordCreate(compressor_id="c1", service_type="1000 Hour Service", service_date="2024-01-15",
                                   running_hours_at_service=1000.0, description="Routine")
    result = await create_service_record(service_record=payload, supabase_client=fake, current_user={})
    assert result["success"] is True
    assert result["data"]["compressor_id"] == "c1"
    assert result["data"]["service_type"] == "1000 Hour Service"
    assert "id" in result["data"]
    assert len(fake.state.tables[SERVICE_RECORDS_TABLE]) == 1


async def test_create_service_record_db_failure_is_500():
    fake = FakeSupabase({})
    fake.always_fail(SERVICE_RECORDS_TABLE, "boom")
    payload = ServiceRecordCreate(compressor_id="c1", service_type="1000 Hour Service", service_date="2024-01-15")
    with pytest.raises(HTTPException) as exc:
        await create_service_record(service_record=payload, supabase_client=fake, current_user={})
    assert exc.value.status_code == 500
