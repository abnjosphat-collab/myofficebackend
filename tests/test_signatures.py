# tests/test_signatures.py — stored-signature validation and the password-unlock gate.
# signatures.py's own top comment: "The password check is the security boundary here.
# Once a signature image is replayable, drawing it proves nothing about identity —
# possession of the password is what attributes the approval to a person." Zero prior
# tests on any of it despite that framing. save_my_signature's input validation
# (source enum, data-URL shape, size cap) and _verify_password's fail-safe when auth
# isn't configured are both real, security-relevant logic covered here.

import pytest

import app.routers.signatures as sig_mod
from app.routers.signatures import (
    SignatureSave, save_my_signature, get_my_signature, _verify_password,
)


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, response_data):
        self._response = response_data

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def upsert(self, *a, **k): return self
    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, response_data):
        self._response = response_data

    def table(self, _name):
        return _FakeTable(self._response)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(response_data=None):
        monkeypatch.setattr(sig_mod, "supabase", _FakeSupabase(response_data))
    return _patch


USER = {"user_id": "u1", "email": "u1@example.com"}


# ─── save_my_signature — input validation (runs before any DB call) ───────────────

async def test_save_rejects_unknown_source(patch_supabase):
    patch_supabase()
    payload = SignatureSave(image_data="data:image/png;base64,abc", source="typed")
    with pytest.raises(Exception) as exc_info:
        await save_my_signature(payload, user=USER)
    assert getattr(exc_info.value, "status_code", None) == 400


async def test_save_rejects_non_data_url_image(patch_supabase):
    patch_supabase()
    payload = SignatureSave(image_data="not-a-data-url", source="drawn")
    with pytest.raises(Exception) as exc_info:
        await save_my_signature(payload, user=USER)
    assert getattr(exc_info.value, "status_code", None) == 400


async def test_save_rejects_oversized_image(patch_supabase):
    patch_supabase()
    huge = "data:image/png;base64," + ("a" * 500_000)
    payload = SignatureSave(image_data=huge, source="drawn")
    with pytest.raises(Exception) as exc_info:
        await save_my_signature(payload, user=USER)
    assert getattr(exc_info.value, "status_code", None) == 413


async def test_save_accepts_valid_signature(patch_supabase):
    patch_supabase()
    payload = SignatureSave(image_data="data:image/png;base64,abc", source="drawn")
    result = await save_my_signature(payload, user=USER)
    assert result == {"ok": True}


# ─── get_my_signature — never leaks the image itself ───────────────────────────────

async def test_get_signature_absent_reports_false(patch_supabase):
    patch_supabase(response_data=[])
    result = await get_my_signature(user=USER)
    assert result == {"has_signature": False}


async def test_get_signature_present_reports_source_and_timestamp_only(patch_supabase):
    patch_supabase(response_data=[{"source": "drawn", "updated_at": "2024-01-01T00:00:00Z"}])
    result = await get_my_signature(user=USER)
    assert result["has_signature"] is True
    assert result["source"] == "drawn"
    assert "image_data" not in result  # the actual security boundary: never returned here


# ─── _verify_password — fails safe when auth isn't configured ─────────────────────

async def test_verify_password_fails_safe_when_env_not_configured(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    with pytest.raises(Exception) as exc_info:
        _verify_password("u1@example.com", "whatever")
    assert getattr(exc_info.value, "status_code", None) == 503


def test_verify_password_returns_true_on_successful_sign_in(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "key")

    class _FakeAuthResult:
        user = {"id": "u1"}

    class _FakeAuth:
        def sign_in_with_password(self, _creds):
            return _FakeAuthResult()

    class _FakeClient:
        auth = _FakeAuth()

    monkeypatch.setattr(sig_mod, "create_client", lambda url, key: _FakeClient())
    assert _verify_password("u1@example.com", "correct-password") is True


def test_verify_password_returns_false_on_wrong_password(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "key")

    class _FakeAuth:
        def sign_in_with_password(self, _creds):
            raise Exception("Invalid login credentials")

    class _FakeClient:
        auth = _FakeAuth()

    monkeypatch.setattr(sig_mod, "create_client", lambda url, key: _FakeClient())
    assert _verify_password("u1@example.com", "wrong-password") is False
