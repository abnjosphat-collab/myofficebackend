# tests/test_uploads.py — the shared upload validation (app/uploads.py): extension
# allowlist + size cap, checked BEFORE the body is read into memory.

import pytest
from fastapi import HTTPException

from app import uploads


class FakeUpload:
    """Minimal stand-in for FastAPI's UploadFile (read(size) mirrors the real one)."""
    def __init__(self, filename, content=b""):
        self.filename = filename
        self._content = content
        self._offset = 0

    async def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._content) - self._offset
        chunk = self._content[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


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


class _AccountingUpload(FakeUpload):
    """Counts every byte the validator pulls — the oversize refusal must come
    after reading at most max_bytes + one chunk, not after buffering it all."""
    def __init__(self, filename, content=b""):
        super().__init__(filename, content)
        self.bytes_pulled = 0

    async def read(self, size=-1):
        chunk = await super().read(size)
        self.bytes_pulled += len(chunk)
        return chunk


async def test_oversize_refusal_stops_reading_early():
    f = _AccountingUpload("big.csv", b"x" * (3 * 1024 * 1024))
    with pytest.raises(HTTPException) as exc:
        await uploads.read_and_validate_upload(f, max_bytes=1024 * 1024, allowed_exts={"csv"})
    assert exc.value.status_code == 400
    assert "too large" in exc.value.detail.lower()
    # 1 MiB cap + at most one 1 MiB chunk over it — never the whole 3 MiB.
    assert f.bytes_pulled <= 2 * 1024 * 1024


async def test_small_file_reads_fully_in_chunks():
    content = b"col1,col2\n" + b"1,2\n" * 1000
    f = _AccountingUpload("data.csv", content)
    assert await uploads.read_and_validate_upload(f, max_bytes=1024 * 1024, allowed_exts={"csv"}) == content
    assert f.bytes_pulled == len(content)


def test_storage_content_type_comes_from_the_extension():
    assert uploads.storage_content_type("memo.pdf") == "application/pdf"
    assert uploads.storage_content_type("photo.JPG") == "image/jpeg"
    assert uploads.storage_content_type("noext") == "application/octet-stream"


def test_storage_content_type_svg_is_forced_to_download():
    # image/svg+xml from a public bucket would run an embedded script in a
    # colleague's browser; octet-stream makes it a download instead.
    assert uploads.storage_content_type("diagram.svg") == "application/octet-stream"
