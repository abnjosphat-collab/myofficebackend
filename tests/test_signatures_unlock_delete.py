# tests/test_signatures_unlock_delete.py — delete_my_signature and
# unlock_my_signature (the actual "hand back the image, given the account password"
# endpoint that is the real security boundary per this file's own top comment) had
# zero tests, along with get_my_signature/save_my_signature's error paths.

import pytest
from fastapi import HTTPException

import app.routers.signatures as sig_mod
from app.routers.signatures import (
    get_my_signature, save_my_signature, delete_my_signature, unlock_my_signature,
    SignatureSave, UnlockRequest,
)

USER = {"user_id": "u1", "email": "u1@example.com"}


class _Resp:
    def __init__(self, data):
        self.data = data


class _FailingTable:
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def upsert(self, *a, **k): return self
    def delete(self): return self
    def execute(self): raise Exception("simulated DB failure")


class _WorkingTable:
    def __init__(self, response_data):
        self._response = response_data

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def delete(self): return self
    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, mode, response_data=None):
        self._mode = mode
        self._response = response_data

    def table(self, _name):
        if self._mode == "fail":
            return _FailingTable()
        return _WorkingTable(self._response)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(mode="working", response_data=None):
        monkeypatch.setattr(sig_mod, "supabase", _FakeSupabase(mode, response_data))
    return _patch


# ─── error paths for the already-tested happy paths ────────────────────────────────

async def test_get_signature_db_failure_raises_500(patch_supabase):
    patch_supabase(mode="fail")
    with pytest.raises(HTTPException) as exc_info:
        await get_my_signature(user=USER)
    assert exc_info.value.status_code == 500


async def test_save_signature_db_failure_raises_500(patch_supabase):
    patch_supabase(mode="fail")
    payload = SignatureSave(image_data="data:image/png;base64,abc", source="drawn")
    with pytest.raises(HTTPException) as exc_info:
        await save_my_signature(payload, user=USER)
    assert exc_info.value.status_code == 500


# ─── delete_my_signature ────────────────────────────────────────────────────────────

async def test_delete_signature_succeeds(patch_supabase):
    patch_supabase(mode="working", response_data=[])
    result = await delete_my_signature(user=USER)
    assert result == {"ok": True}


async def test_delete_signature_db_failure_raises_500(patch_supabase):
    patch_supabase(mode="fail")
    with pytest.raises(HTTPException) as exc_info:
        await delete_my_signature(user=USER)
    assert exc_info.value.status_code == 500


# ─── unlock_my_signature — the real security boundary ──────────────────────────────

async def test_unlock_wrong_password_is_401_before_any_db_call(monkeypatch, patch_supabase):
    monkeypatch.setattr(sig_mod, "_verify_password", lambda email, pw: False)
    patch_supabase(mode="fail")  # would raise if reached — proves the DB is never queried
    with pytest.raises(HTTPException) as exc_info:
        await unlock_my_signature(UnlockRequest(password="wrong"), user=USER)
    assert exc_info.value.status_code == 401


async def test_unlock_correct_password_no_saved_signature_is_404(monkeypatch, patch_supabase):
    monkeypatch.setattr(sig_mod, "_verify_password", lambda email, pw: True)
    patch_supabase(mode="working", response_data=[])
    with pytest.raises(HTTPException) as exc_info:
        await unlock_my_signature(UnlockRequest(password="correct"), user=USER)
    assert exc_info.value.status_code == 404


async def test_unlock_correct_password_returns_the_image(monkeypatch, patch_supabase):
    monkeypatch.setattr(sig_mod, "_verify_password", lambda email, pw: True)
    patch_supabase(mode="working", response_data=[{"image_data": "data:image/png;base64,xyz", "source": "drawn"}])
    result = await unlock_my_signature(UnlockRequest(password="correct"), user=USER)
    assert result == {"image_data": "data:image/png;base64,xyz", "source": "drawn"}


async def test_unlock_db_lookup_failure_raises_500(monkeypatch, patch_supabase):
    monkeypatch.setattr(sig_mod, "_verify_password", lambda email, pw: True)
    patch_supabase(mode="fail")
    with pytest.raises(HTTPException) as exc_info:
        await unlock_my_signature(UnlockRequest(password="correct"), user=USER)
    assert exc_info.value.status_code == 500
