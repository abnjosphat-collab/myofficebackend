# tests/test_photos_and_lookup_lists_errors.py — error paths for photos.py's upload/
# delete and lookup_lists.py's four endpoints, none of which had failure-path coverage
# (only happy-path/404 branches were tested).

import pytest
from fastapi import HTTPException

import app.routers.photos as photos_mod
from app.routers.photos import upload_photo, delete_photo

import app.routers.lookup_lists as ll_mod
from app.routers.lookup_lists import (
    get_lookup_list, add_lookup_value, rename_lookup_value, delete_lookup_value, LookupValueCreate,
)


# ─── photos.py ───────────────────────────────────────────────────────────────────────

class _FakeUploadFile:
    def __init__(self, filename: str, content: bytes = b"x"):
        self.filename = filename
        self.content_type = "image/jpeg"
        self._content = content

    async def read(self) -> bytes:
        return self._content


class _FailingBucket:
    def upload(self, *a, **k): raise Exception("simulated storage failure")
    def get_public_url(self, *a, **k): raise Exception("simulated URL generation failure")
    def remove(self, *a, **k): raise Exception("simulated storage failure")


class _WorkingUploadFailingUrlBucket:
    """Upload succeeds, but get_public_url fails — a non-fatal secondary failure."""
    def upload(self, *a, **k): return None
    def get_public_url(self, *a, **k): raise Exception("simulated URL generation failure")


class _FakeStorage:
    def __init__(self, bucket):
        self._bucket = bucket

    def from_(self, _bucket_name):
        return self._bucket


class _FakeSupabase:
    def __init__(self, bucket):
        self.storage = _FakeStorage(bucket)


async def test_upload_storage_failure_raises_500(monkeypatch):
    monkeypatch.setattr(photos_mod, "supabase", _FakeSupabase(_FailingBucket()))
    file = _FakeUploadFile("photo.jpg")
    with pytest.raises(HTTPException) as exc_info:
        await upload_photo(file=file, folder="misc", current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


async def test_upload_get_public_url_failure_is_non_fatal(monkeypatch):
    # The photo itself uploaded fine — a failure generating its display URL must not
    # fail the whole upload, just come back with an empty url.
    monkeypatch.setattr(photos_mod, "supabase", _FakeSupabase(_WorkingUploadFailingUrlBucket()))
    file = _FakeUploadFile("photo.jpg")
    result = await upload_photo(file=file, folder="misc", current_user={"user_id": "u1"})
    assert result["url"] == ""
    assert result["path"]


async def test_delete_storage_failure_raises_500(monkeypatch):
    monkeypatch.setattr(photos_mod, "supabase", _FakeSupabase(_FailingBucket()))
    with pytest.raises(HTTPException) as exc_info:
        await delete_photo(path="misc/x.jpg", current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


# ─── lookup_lists.py ─────────────────────────────────────────────────────────────────

class _FailingTable:
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def ilike(self, *a, **k): return self
    def order(self, *a, **k): return self
    def update(self, *a, **k): return self
    def insert(self, *a, **k): return self
    def delete(self): return self
    def execute(self): raise Exception("simulated DB failure")


class _FailingSupabase:
    def table(self, _name):
        return _FailingTable()


@pytest.fixture
def patch_failing_supabase(monkeypatch):
    monkeypatch.setattr(ll_mod, "supabase", _FailingSupabase())


async def test_get_lookup_list_db_failure_raises_500(patch_failing_supabase):
    with pytest.raises(HTTPException) as exc_info:
        await get_lookup_list("location")
    assert exc_info.value.status_code == 500


async def test_add_lookup_value_db_failure_raises_500(patch_failing_supabase):
    with pytest.raises(HTTPException) as exc_info:
        await add_lookup_value("location", LookupValueCreate(value="New Value"))
    assert exc_info.value.status_code == 500


async def test_rename_lookup_value_db_failure_raises_500(patch_failing_supabase):
    with pytest.raises(HTTPException) as exc_info:
        await rename_lookup_value("location", 1, LookupValueCreate(value="New Name"))
    assert exc_info.value.status_code == 500


async def test_delete_lookup_value_db_failure_raises_500(patch_failing_supabase):
    with pytest.raises(HTTPException) as exc_info:
        await delete_lookup_value("location", 1)
    assert exc_info.value.status_code == 500
