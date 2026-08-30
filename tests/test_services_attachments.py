# tests/test_services_attachments.py — the attachment endpoints (list_attachments,
# upload_attachment, delete_attachment) and the /ocr endpoint on services.py, all
# previously untested (services.py was 44% covered). The OCR tests deliberately use the
# REAL PyMuPDF (fitz) / Pillow / numpy libraries rather than mocking them — they're
# actually installed in this venv — to exercise the genuine "digital PDF, no OCR needed"
# text-extraction path end to end. `easyocr` is DELIBERATELY absent from this venv (see
# services.py's own comment — it drags in PyTorch), so the "scanned page / image" path
# naturally hits a real ImportError inside _get_ocr_reader(); that's used here as a real
# (not simulated) exercise of the 502 error-handling branch, not a mock.

import io

import pytest
from fastapi import HTTPException

from app.routers import services as services_mod
from app.routers.services import list_attachments, upload_attachment, delete_attachment, ocr_document


CURRENT_USER = {"user_id": "u1", "email": "u1@x.com", "role": "user"}
MANAGER_USER = {"user_id": "m1", "email": "m1@x.com", "role": "manager"}


# ─── Fake supabase — table() for service_attachments, storage for the attachments bucket ──

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table_name, state, cfg):
        self.table_name = table_name
        self.state = state
        self.cfg = cfg
        self._op = "select"
        self._filters = []
        self._payload = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        if self.cfg.get("raise_op") == self._op:
            raise Exception(self.cfg.get("raise_msg", "boom"))
        if self._op == "insert":
            return _Resp(self.cfg.get("insert_return"))
        if self._op == "delete":
            return _Resp(self.cfg.get("delete_return", [{"id": "a1"}]))
        return _Resp(self.cfg.get("select_return"))


class _FakeBucket:
    def __init__(self, cfg):
        self.cfg = cfg

    def upload(self, path, content, opts):
        if self.cfg.get("upload_raises"):
            raise Exception(self.cfg["upload_raises"])
        self.cfg.setdefault("uploaded", []).append({"path": path, "content": content, "opts": opts})
        return {"path": path}

    def get_public_url(self, path):
        if self.cfg.get("public_url_raises"):
            raise Exception(self.cfg["public_url_raises"])
        return self.cfg.get("public_url_return", f"https://cdn.example.com/{path}")

    def remove(self, paths):
        if self.cfg.get("remove_raises"):
            raise Exception(self.cfg["remove_raises"])
        self.cfg.setdefault("removed", []).append(paths)


class _FakeStorage:
    def __init__(self, bucket_cfg):
        self.bucket_cfg = bucket_cfg

    def from_(self, bucket_name):
        return _FakeBucket(self.bucket_cfg)


class _FakeSupabase:
    def __init__(self, table_cfg, bucket_cfg=None):
        self.state = {"calls": []}
        self.cfg = table_cfg
        self.storage = _FakeStorage(bucket_cfg or {})

    def table(self, name):
        assert name == "service_attachments"
        return _Query(name, self.state, self.cfg)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _apply(table_cfg: dict, bucket_cfg: dict = None) -> _FakeSupabase:
        fake = _FakeSupabase(table_cfg, bucket_cfg)
        monkeypatch.setattr(services_mod, "supabase", fake)
        return fake
    return _apply


class _FakeUploadFile:
    def __init__(self, filename, content: bytes, content_type="application/octet-stream"):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self):
        return self._content


# ─── list_attachments ────────────────────────────────────────────────────────────────

async def test_list_attachments_returns_rows_for_the_service(patch_supabase):
    fake = patch_supabase({"select_return": [{"id": "a1", "filename": "invoice.pdf"}]})
    result = await list_attachments("s1")
    assert result == [{"id": "a1", "filename": "invoice.pdf"}]
    select_call = fake.state["calls"][0]
    assert ("service_id", "s1") in select_call["filters"]


async def test_list_attachments_empty_is_empty_list(patch_supabase):
    patch_supabase({"select_return": None})
    assert await list_attachments("s1") == []


async def test_list_attachments_db_error_is_500(patch_supabase):
    patch_supabase({"raise_op": "select", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await list_attachments("s1")
    assert exc.value.status_code == 500


# ─── upload_attachment ───────────────────────────────────────────────────────────────

async def test_upload_attachment_happy_path(patch_supabase):
    fake = patch_supabase(
        {"insert_return": [{"id": "a1", "filename": "invoice.pdf"}]},
        bucket_cfg={},
    )
    file = _FakeUploadFile("invoice.pdf", b"pdf-bytes", content_type="application/pdf")

    result = await upload_attachment("s1", file, current_user=CURRENT_USER)

    assert result == {"id": "a1", "filename": "invoice.pdf"}
    insert_payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert insert_payload["service_id"] == "s1"
    assert insert_payload["filename"] == "invoice.pdf"
    assert insert_payload["file_size"] == len(b"pdf-bytes")
    assert insert_payload["storage_path"].startswith("s1/")
    assert insert_payload["storage_path"].endswith(".pdf")
    assert insert_payload["file_url"] == f"https://cdn.example.com/{insert_payload['storage_path']}"


async def test_upload_attachment_rejects_disallowed_extension(patch_supabase):
    patch_supabase({}, bucket_cfg={})
    file = _FakeUploadFile("script.exe", b"x" * 10, content_type="application/octet-stream")
    with pytest.raises(HTTPException) as exc:
        await upload_attachment("s1", file, current_user=CURRENT_USER)
    assert exc.value.status_code == 400


async def test_upload_attachment_rejects_oversized_file(patch_supabase):
    patch_supabase({}, bucket_cfg={})
    file = _FakeUploadFile("big.pdf", b"x" * (51 * 1024 * 1024), content_type="application/pdf")
    with pytest.raises(HTTPException) as exc:
        await upload_attachment("s1", file, current_user=CURRENT_USER)
    assert exc.value.status_code == 400
    assert "too large" in exc.value.detail.lower()


async def test_upload_attachment_storage_failure_is_500(patch_supabase):
    patch_supabase({}, bucket_cfg={"upload_raises": "storage offline"})
    file = _FakeUploadFile("invoice.pdf", b"pdf-bytes", content_type="application/pdf")
    with pytest.raises(HTTPException) as exc:
        await upload_attachment("s1", file, current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    assert "storage offline" in exc.value.detail


async def test_upload_attachment_public_url_failure_still_succeeds_with_empty_url(patch_supabase):
    # Documented intentional fallback: the file itself is safely uploaded and the row
    # is still inserted — only the display URL is empty — so this must NOT raise.
    fake = patch_supabase(
        {"insert_return": [{"id": "a1"}]},
        bucket_cfg={"public_url_raises": "cdn hiccup"},
    )
    file = _FakeUploadFile("invoice.pdf", b"pdf-bytes", content_type="application/pdf")

    result = await upload_attachment("s1", file, current_user=CURRENT_USER)

    assert result == {"id": "a1"}
    insert_payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert insert_payload["file_url"] == ""


async def test_upload_attachment_filename_without_extension_defaults_to_bin(patch_supabase):
    fake = patch_supabase({"insert_return": [{"id": "a1"}]}, bucket_cfg={})
    file = _FakeUploadFile("noextension", b"content", content_type="application/octet-stream")
    with pytest.raises(HTTPException):
        # DOCUMENT_EXTS validation happens first via read_and_validate_upload and a
        # filename with no extension is rejected there — confirms the allowlist check
        # runs before the "default to .bin" fallback further down ever gets reached.
        await upload_attachment("s1", file, current_user=CURRENT_USER)


# ─── delete_attachment ───────────────────────────────────────────────────────────────

async def test_delete_attachment_removes_storage_object_and_row(patch_supabase):
    fake = patch_supabase(
        {"select_return": {"storage_path": "s1/abc.pdf"}},
        bucket_cfg={},
    )
    result = await delete_attachment("s1", "a1", current_user=MANAGER_USER)
    assert result == {"ok": True}
    assert fake.storage.bucket_cfg["removed"] == [["s1/abc.pdf"]]
    delete_call = next(c for c in fake.state["calls"] if c["op"] == "delete")
    assert ("id", "a1") in delete_call["filters"]


async def test_delete_attachment_storage_failure_does_not_block_row_deletion(patch_supabase):
    # Documented intentional fallback: a storage-delete failure is logged and the row
    # delete still proceeds (see delete_attachment's own comment).
    fake = patch_supabase(
        {"select_return": {"storage_path": "s1/abc.pdf"}},
        bucket_cfg={"remove_raises": "storage locked"},
    )
    result = await delete_attachment("s1", "a1", current_user=MANAGER_USER)
    assert result == {"ok": True}
    row_delete_calls = [c for c in fake.state["calls"] if c["table"] == "service_attachments" and c["op"] == "delete"]
    assert len(row_delete_calls) == 1


async def test_delete_attachment_missing_row_still_deletes_without_storage_call(patch_supabase):
    fake = patch_supabase({"select_return": None}, bucket_cfg={})
    result = await delete_attachment("s1", "missing", current_user=MANAGER_USER)
    assert result == {"ok": True}
    assert "removed" not in fake.storage.bucket_cfg


async def test_delete_attachment_db_error_is_500(patch_supabase):
    patch_supabase({"select_return": {"storage_path": "x"}, "raise_op": "delete", "raise_msg": "delete failed"}, bucket_cfg={})
    with pytest.raises(HTTPException) as exc:
        await delete_attachment("s1", "a1", current_user=MANAGER_USER)
    assert exc.value.status_code == 500


# ─── ocr_document ─────────────────────────────────────────────────────────────────────

def _make_digital_pdf_bytes(text: str) -> bytes:
    """A real, minimal single-page PDF with an embedded text layer, built with the same
    PyMuPDF library the endpoint itself uses — exercises the genuine 'digital PDF, no
    OCR needed' extraction path rather than mocking fitz."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _make_blank_pdf_bytes() -> bytes:
    """A real PDF page with NO text at all — forces ocr_document down the 'scanned page'
    branch, which then genuinely fails (easyocr is deliberately not installed here)."""
    import fitz
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


async def test_ocr_document_rejects_oversized_file():
    file = _FakeUploadFile("big.pdf", b"x" * (21 * 1024 * 1024), content_type="application/pdf")
    with pytest.raises(HTTPException) as exc:
        await ocr_document(file, current_user=CURRENT_USER)
    assert exc.value.status_code == 400
    assert "too large" in exc.value.detail.lower()


async def test_ocr_document_rejects_unsupported_mime_type():
    file = _FakeUploadFile("data.txt", b"plain text content", content_type="text/plain")
    with pytest.raises(HTTPException) as exc:
        await ocr_document(file, current_user=CURRENT_USER)
    assert exc.value.status_code == 400
    assert "Unsupported type" in exc.value.detail


async def test_ocr_document_extracts_text_from_a_real_digital_pdf():
    pdf_bytes = _make_digital_pdf_bytes("Supplier: Bearing Solutions Pty Ltd")
    file = _FakeUploadFile("service.pdf", pdf_bytes, content_type="application/pdf")

    result = await ocr_document(file, current_user=CURRENT_USER)

    assert result["supplier"] == "Bearing Solutions Pty Ltd"


async def test_ocr_document_scanned_page_hits_missing_easyocr_dependency_as_502():
    # easyocr is genuinely not installed in this venv (see services.py's own comment on
    # _get_ocr_reader) — a blank-text PDF page forces the OCR fallback path, which must
    # surface as a clean 502, not an unhandled ImportError.
    pdf_bytes = _make_blank_pdf_bytes()
    file = _FakeUploadFile("scanned.pdf", pdf_bytes, content_type="application/pdf")

    with pytest.raises(HTTPException) as exc:
        await ocr_document(file, current_user=CURRENT_USER)
    assert exc.value.status_code == 502
    assert "OCR extraction failed" in exc.value.detail


async def test_ocr_document_image_mime_also_requires_ocr_and_hits_missing_dependency():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="white").save(buf, format="PNG")
    file = _FakeUploadFile("photo.png", buf.getvalue(), content_type="image/png")

    with pytest.raises(HTTPException) as exc:
        await ocr_document(file, current_user=CURRENT_USER)
    assert exc.value.status_code == 502
