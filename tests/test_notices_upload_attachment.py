# tests/test_notices_upload_attachment.py — upload_notice_attachment previously
# didn't exist at all: the frontend read a picked File's name/size into the form
# and never uploaded it anywhere, so attachment_url stayed blank and "Download
# attachment" had nothing to link to. Same "fake Storage bucket" recipe as
# test_documents_endpoints.py's upload_document tests, minus a table (this
# endpoint never writes to `documents` — see notices.py's ATTACHMENT_BUCKET note).

import pytest
from fastapi import HTTPException

import app.routers.notices as notices_mod
from app.routers.notices import upload_notice_attachment, ATTACHMENT_BUCKET


class _FakeStorageBucket:
    def __init__(self, state):
        self.state = state

    def upload(self, path, content, opts):
        if self.state.get("upload_raises"):
            raise Exception(self.state["upload_raises"])
        self.state.setdefault("uploaded", []).append((path, content, opts))

    def get_public_url(self, path):
        if self.state.get("public_url_raises"):
            raise Exception("url generation failed")
        return f"https://cdn.example.com/{path}"


class _FakeStorage:
    def __init__(self, state):
        self.state = state

    def from_(self, bucket):
        self.state["storage_bucket"] = bucket
        return _FakeStorageBucket(self.state)


class _FakeSupabase:
    def __init__(self, state):
        self.storage = _FakeStorage(state)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(**extra_state):
        state = dict(extra_state)
        monkeypatch.setattr(notices_mod, "supabase", _FakeSupabase(state))
        return state
    return _patch


class _FakeUploadFile:
    def __init__(self, filename, content, content_type="application/pdf"):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self):
        return self._content


async def test_upload_attachment_happy_path(patch_supabase):
    state = patch_supabase()
    result = await upload_notice_attachment(file=_FakeUploadFile("memo.pdf", b"hello world"))
    assert result["attachment_name"] == "memo.pdf"
    assert result["attachment_url"] == f"https://cdn.example.com/{state['uploaded'][0][0]}"
    assert result["attachment_size"] == f"{len(b'hello world') / (1024 * 1024):.2f} MB"
    assert state["storage_bucket"] == ATTACHMENT_BUCKET
    assert state["uploaded"][0][0].startswith("notices/")


async def test_upload_attachment_accepts_a_wider_range_than_documents(patch_supabase):
    # mp4/mp3/odt etc. are outside documents.py's DOCUMENT_EXTS but inside
    # NOTICE_ATTACHMENT_EXTS — this is the actual "allow all kinds of attachment
    # file types" fix, verified against the real validator, not just eyeballing
    # the allowlist definition.
    patch_supabase()
    for filename in ("briefing.mp4", "voice_note.mp3", "policy.odt", "photo.heic"):
        result = await upload_notice_attachment(file=_FakeUploadFile(filename, b"x"))
        assert result["attachment_name"] == filename


async def test_upload_attachment_still_rejects_executables(patch_supabase):
    # Broadening the allowlist must not become "accept anything" — this is the
    # one non-negotiable boundary.
    state = patch_supabase()
    with pytest.raises(HTTPException) as exc:
        await upload_notice_attachment(file=_FakeUploadFile("installer.exe", b"x"))
    assert exc.value.status_code == 400
    assert "uploaded" not in state  # never reached the storage layer


async def test_upload_attachment_storage_failure_raises_500(patch_supabase):
    patch_supabase(upload_raises="disk full")
    with pytest.raises(HTTPException) as exc:
        await upload_notice_attachment(file=_FakeUploadFile("memo.pdf", b"x"))
    assert exc.value.status_code == 500
    assert "Storage upload failed" in exc.value.detail


async def test_upload_attachment_public_url_failure_still_returns_empty_url(patch_supabase):
    patch_supabase(public_url_raises=True)
    result = await upload_notice_attachment(file=_FakeUploadFile("memo.pdf", b"x"))
    assert result["attachment_url"] == ""
    assert result["attachment_name"] == "memo.pdf"
