# tests/test_compressors_export_import.py — export_data (CSV StreamingResponse),
# import_data (CSV upload -> bulk insert), and create_service_record. All zero
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


# ─── import_data ──────────────────────────────────────────────────────────────────────

async def test_import_inserts_valid_rows_with_expected_field_mapping():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    csv_content = (
        "name,model,capacity,status,location,running,loaded\n"
        "Comp A,X1,500cfm,running,Plant 1,120,90\n"
    ).encode()
    file = _FakeUploadFile("import.csv", csv_content)
    result = await _import_data(request=None, file=file, supabase_client=fake, current_user={})
    assert result["success"] is True
    assert result["imported_count"] == 1
    assert result["errors"] == []
    row = fake.state.tables[COMPRESSORS_TABLE][0]
    assert row["name"] == "Comp A"
    assert row["status"] == "running"
    assert row["total_running_hours"] == 120.0
    assert row["total_loaded_hours"] == 90.0
    assert row["initial_total_running"] == 120.0
    assert row["initial_total_loaded"] == 90.0


async def test_import_unknown_status_falls_back_to_standby():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    csv_content = "h1,h2,h3,h4,h5,h6,h7\nComp A,X1,500cfm,bogus-status,Plant 1,10,5\n".encode()
    file = _FakeUploadFile("import.csv", csv_content)
    result = await _import_data(request=None, file=file, supabase_client=fake, current_user={})
    assert result["imported_count"] == 1
    assert fake.state.tables[COMPRESSORS_TABLE][0]["status"] == "standby"


async def test_import_row_with_too_few_columns_is_silently_skipped():
    # documents actual behavior: a short row fails the `len(values) >= 6` gate and is
    # neither imported NOR reported in `errors` -- it just silently vanishes.
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    csv_content = "h1,h2,h3,h4,h5,h6,h7\nComp A,X1,500cfm\n".encode()
    file = _FakeUploadFile("import.csv", csv_content)
    result = await _import_data(request=None, file=file, supabase_client=fake, current_user={})
    assert result["imported_count"] == 0
    assert result["errors"] == []
    assert fake.state.tables[COMPRESSORS_TABLE] == []


async def test_import_row_with_unparseable_number_is_reported_in_errors_not_raised():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    csv_content = "h1,h2,h3,h4,h5,h6,h7\nComp A,X1,500cfm,running,Plant 1,not-a-number,5\n".encode()
    file = _FakeUploadFile("import.csv", csv_content)
    result = await _import_data(request=None, file=file, supabase_client=fake, current_user={})
    assert result["imported_count"] == 0
    assert len(result["errors"]) == 1
    assert "Line 2" in result["errors"][0]


async def test_import_continues_after_a_bad_row():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    csv_content = (
        "h1,h2,h3,h4,h5,h6,h7\n"
        "Bad,X1,500cfm,running,Plant 1,not-a-number,5\n"
        "Good,X1,500cfm,running,Plant 1,10,5\n"
    ).encode()
    file = _FakeUploadFile("import.csv", csv_content)
    result = await _import_data(request=None, file=file, supabase_client=fake, current_user={})
    assert result["imported_count"] == 1
    assert len(result["errors"]) == 1
    assert fake.state.tables[COMPRESSORS_TABLE][0]["name"] == "Good"


async def test_import_missing_optional_hours_columns_default_to_zero():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    csv_content = "h1,h2,h3,h4,h5,h6\nComp A,X1,500cfm,running,Plant 1,\n".encode()
    file = _FakeUploadFile("import.csv", csv_content)
    result = await _import_data(request=None, file=file, supabase_client=fake, current_user={})
    assert result["imported_count"] == 1
    row = fake.state.tables[COMPRESSORS_TABLE][0]
    assert row["total_running_hours"] == 0.0
    assert row["total_loaded_hours"] == 0.0


async def test_import_rejects_non_csv_extension():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    file = _FakeUploadFile("import.exe", b"whatever")
    with pytest.raises(HTTPException) as exc:
        await _import_data(request=None, file=file, supabase_client=fake, current_user={})
    assert exc.value.status_code == 400


async def test_import_generic_upload_failure_is_500_not_a_400():
    # A failure that happens outside the per-row parse/insert loop (which catches its
    # own errors into `errors[]`) and isn't the extension/size/utf-8 checks (which are
    # already real HTTPExceptions) -- e.g. the upload stream itself breaking mid-read --
    # must still surface as a real 500, not be swallowed.
    class _BrokenUploadFile:
        filename = "import.csv"
        async def read(self):
            raise OSError("stream reset by peer")

    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    with pytest.raises(HTTPException) as exc:
        await _import_data(request=None, file=_BrokenUploadFile(), supabase_client=fake, current_user={})
    assert exc.value.status_code == 500


async def test_import_rejects_non_utf8_file():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    file = _FakeUploadFile("import.csv", b"\xff\xfe\x00bad-bytes")
    with pytest.raises(HTTPException) as exc:
        await _import_data(request=None, file=file, supabase_client=fake, current_user={})
    assert exc.value.status_code == 400
    assert "UTF-8" in exc.value.detail


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
