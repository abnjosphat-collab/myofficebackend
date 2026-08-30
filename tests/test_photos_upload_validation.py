# tests/test_photos_upload_validation.py — upload_photo's extension whitelist and
# 20MB size cap (both run BEFORE any Supabase Storage call), plus a successful upload
# and delete against a fake storage client. Zero prior tests despite being the shared
# photo-upload endpoint used across the app.

import pytest

import app.routers.photos as photos_mod
from app.routers.photos import upload_photo, delete_photo


class _FakeUploadFile:
    def __init__(self, filename: str, content: bytes, content_type: str = "image/jpeg"):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self) -> bytes:
        return self._content


class _FakeStorageBucket:
    def __init__(self, state):
        self.state = state

    def upload(self, path, content, options):
        self.state["uploaded_path"] = path
        self.state["uploaded_bytes"] = content

    def get_public_url(self, path):
        return f"https://fake.storage/{path}"

    def remove(self, paths):
        self.state["removed_paths"] = paths


class _FakeStorage:
    def __init__(self, state):
        self.state = state

    def from_(self, _bucket):
        return _FakeStorageBucket(self.state)


class _FakeSupabase:
    def __init__(self, state):
        self.storage = _FakeStorage(state)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {}
    monkeypatch.setattr(photos_mod, "supabase", _FakeSupabase(state))
    return state


async def test_rejects_unsupported_extension_before_reading_the_file(patch_supabase):
    file = _FakeUploadFile("malware.exe", b"x")
    with pytest.raises(Exception) as exc_info:
        await upload_photo(file=file, folder="misc", current_user={"user_id": "u1"})
    assert getattr(exc_info.value, "status_code", None) == 400
    assert "uploaded_path" not in patch_supabase  # never reached the storage call


async def test_rejects_oversized_file(patch_supabase):
    file = _FakeUploadFile("photo.jpg", b"x" * (20 * 1024 * 1024 + 1))
    with pytest.raises(Exception) as exc_info:
        await upload_photo(file=file, folder="misc", current_user={"user_id": "u1"})
    assert getattr(exc_info.value, "status_code", None) == 400


async def test_accepts_file_at_exactly_the_size_limit(patch_supabase):
    file = _FakeUploadFile("photo.jpg", b"x" * (20 * 1024 * 1024))
    result = await upload_photo(file=file, folder="misc", current_user={"user_id": "u1"})
    assert result["url"].startswith("https://fake.storage/")


async def test_successful_upload_returns_url_and_storage_path(patch_supabase):
    file = _FakeUploadFile("photo.jpg", b"fake-image-bytes")
    result = await upload_photo(file=file, folder="ppe", current_user={"user_id": "u1"})
    assert result["path"].startswith("ppe/")
    assert result["path"].endswith(".jpg")
    assert result["url"] == f"https://fake.storage/{result['path']}"


async def test_no_extension_defaults_to_jpg(patch_supabase):
    # A filename with no extension at all defaults to treating it as .jpg rather than
    # crashing on the extension-split.
    file = _FakeUploadFile("noextension", b"x")
    result = await upload_photo(file=file, folder="misc", current_user={"user_id": "u1"})
    assert result["path"].endswith(".jpg")


async def test_case_insensitive_extension_is_accepted(patch_supabase):
    file = _FakeUploadFile("photo.JPG", b"x")
    result = await upload_photo(file=file, folder="misc", current_user={"user_id": "u1"})
    assert result["path"].endswith(".jpg")  # normalized to lowercase


# ─── delete_photo ────────────────────────────────────────────────────────────────────

async def test_delete_calls_storage_remove_with_the_given_path(patch_supabase):
    result = await delete_photo(path="misc/abc123.jpg", current_user={"user_id": "u1"})
    assert result == {"ok": True}
    assert patch_supabase["removed_paths"] == ["misc/abc123.jpg"]
