# tests/test_rate_limit.py — public-endpoint rate limits (slowapi per-IP) and the
# Tools-login failed-attempt counter (in-process per-username).
#
# The slowapi limits are exercised through TestClient against the real app
# with the limiter explicitly enabled and its storage reset per test (other
# test files keep it disabled — see the disable_limiter fixtures). The
# failed-login counter is in-process state on the tools_workspace module,
# reset by _reset_for_tests() like the rest of its memory store.
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.rate_limit import limiter, client_ip
from app.routers import tools_workspace
import app.routers.admin as admin_mod
from main import app

client = TestClient(app)


def setup_function():
    tools_workspace._reset_for_tests()


@pytest.fixture
def live_limiter():
    original = limiter.enabled
    limiter.enabled = True
    limiter.reset()
    yield
    limiter.enabled = original
    limiter.reset()


@pytest.fixture
def limiter_off():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


# ─── client_ip: proxy header only when trusted ──────────────────────────────

def _request(client_host, xff=None):
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "headers": headers, "client": (client_host, 1234)})


def test_client_ip_ignores_forwarded_header_by_default(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    assert client_ip(_request("9.9.9.9", xff="1.2.3.4")) == "9.9.9.9"


@pytest.mark.parametrize("flag", ["true", "1", "yes", "TRUE"])
def test_client_ip_trusts_first_hop_when_enabled(monkeypatch, flag):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", flag)
    assert client_ip(_request("9.9.9.9", xff="1.2.3.4")) == "1.2.3.4"


def test_client_ip_takes_first_hop_only(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    assert client_ip(_request("9.9.9.9", xff="1.2.3.4, 5.6.7.8")) == "1.2.3.4"


def test_client_ip_falls_back_to_peer_without_header(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    assert client_ip(_request("9.9.9.9")) == "9.9.9.9"


# ─── slowapi per-route limits ───────────────────────────────────────────────

class _EmptyTable:
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def execute(self):
        class R: data = []
        return R()


class _EmptySupabase:
    def table(self, _name): return _EmptyTable()


def test_admin_password_reset_request_is_5_per_minute(monkeypatch, live_limiter):
    # Unknown email → early {"ok": True} without touching the DB rows; the
    # limit is what varies across the 6 calls, not the handler logic.
    monkeypatch.setattr(admin_mod, "supabase", _EmptySupabase())
    statuses = [
        client.post("/api/admin/password-reset-requests", json={"email": "x@y.com"}).status_code
        for _ in range(6)
    ]
    assert statuses == [200, 200, 200, 200, 200, 429]


def test_tools_register_is_5_per_minute(live_limiter):
    statuses = []
    for i in range(6):
        r = client.post("/api/tools-workspace/auth/register", json={
            "name": f"User {i}", "username": f"rateuser{i}", "password": "secret12",
        })
        statuses.append(r.status_code)
    assert statuses == [201, 201, 201, 201, 201, 429]


def test_tools_login_is_10_per_minute(live_limiter):
    limiter.enabled = False  # setup must not consume the login/register budget
    try:
        r = client.post("/api/tools-workspace/auth/register", json={
            "name": "Login User", "username": "loginuser", "password": "secret12",
        })
        assert r.status_code == 201
    finally:
        limiter.enabled = True
    limiter.reset()
    statuses = [
        client.post("/api/tools-workspace/auth/login",
                    json={"username": "loginuser", "password": "secret12"}).status_code
        for _ in range(11)
    ]
    assert statuses == [200] * 10 + [429]


# ─── Tools failed-login counter (per username, in-process) ──────────────────

def _login(username, password):
    return client.post("/api/tools-workspace/auth/login",
                       json={"username": username, "password": password})


def test_five_failures_then_429_even_with_right_password(limiter_off):
    client.post("/api/tools-workspace/auth/register", json={
        "name": "Victim", "username": "victim", "password": "secret12",
    })
    for _ in range(5):
        assert _login("victim", "wrong-password").status_code == 401
    # The 6th attempt is gated before the password is even checked.
    assert _login("victim", "secret12").status_code == 429


def test_counter_is_per_username(limiter_off):
    client.post("/api/tools-workspace/auth/register", json={
        "name": "Victim", "username": "victim", "password": "secret12",
    })
    client.post("/api/tools-workspace/auth/register", json={
        "name": "Other", "username": "other", "password": "secret12",
    })
    for _ in range(5):
        _login("victim", "wrong-password")
    assert _login("victim", "secret12").status_code == 429
    assert _login("other", "secret12").status_code == 200


def test_unknown_usernames_count_too(limiter_off):
    for _ in range(5):
        assert _login("ghost", "whatever").status_code == 401
    assert _login("ghost", "whatever").status_code == 429


def test_success_clears_the_counter(limiter_off):
    client.post("/api/tools-workspace/auth/register", json={
        "name": "Victim", "username": "victim", "password": "secret12",
    })
    for _ in range(4):
        _login("victim", "wrong-password")
    assert _login("victim", "secret12").status_code == 200  # clears the 4
    for _ in range(4):
        assert _login("victim", "wrong-password").status_code == 401
    assert _login("victim", "secret12").status_code == 200  # still allowed


def test_failures_expire_after_the_window(limiter_off):
    import time as time_mod

    client.post("/api/tools-workspace/auth/register", json={
        "name": "Victim", "username": "victim", "password": "secret12",
    })
    # Five failures, but all older than the 15-minute window.
    tools_workspace._login_failures["victim"] = [
        time_mod.monotonic() - 16 * 60 for _ in range(5)
    ]
    assert _login("victim", "secret12").status_code == 200
