# tests/test_uploads.py — the shared upload validation (app/uploads.py): extension
# allowlist + size cap, checked BEFORE the body is read into memory.

import pytest
from fastapi import HTTPException

from app import uploads


class FakeUpload:
    """Minimal stand-in for FastAPI's UploadFile."""
    def __init__(self, filename, content=b""):
        self.filename = filename
        self._content = content

    async def read(self):
        return self._content


async def test_rejects_disallowed_extension():
    f = FakeUpload("evil.exe", b"data")
    with pytest.raises(HTTPException) as exc:
        await uploads.read_and_validate_upload(f, max_bytes=1024, allowed_exts={"csv"})
    assert exc.value.status_code == 400
    assert "Unsupported file type" in exc.value.detail


async def test_rejects_no_extension():
    f = FakeUpload("noext", b"data")
    with pytest.raises(HTTPException) as exc:
        await uploads.read_and_validate_upload(f, max_bytes=1024, allowed_exts={"csv"})
    assert exc.value.status_code == 400


async def test_rejects_oversize():
    f = FakeUpload("big.csv", b"x" * 2048)
    with pytest.raises(HTTPException) as exc:
        await uploads.read_and_validate_upload(f, max_bytes=1024, allowed_exts={"csv"})
    assert exc.value.status_code == 400
    assert "too large" in exc.value.detail.lower()


async def test_accepts_valid_file_and_returns_bytes():
    f = FakeUpload("data.CSV", b"col1,col2\n1,2\n")  # extension check is case-insensitive
    content = await uploads.read_and_validate_upload(f, max_bytes=1024, allowed_exts={"csv"})
    assert content == b"col1,col2\n1,2\n"


def test_predefined_allowlists_are_sane():
    assert "csv" in uploads.DOCUMENT_EXTS
    assert "xlsx" in uploads.SPREADSHEET_EXTS
    assert "exe" not in uploads.DOCUMENT_EXTS
