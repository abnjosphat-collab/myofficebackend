# tests/test_scope_wiring.py — Batch 1: the scope plumbing behind enforcement.
#
# Covers what Batch 1 added, not the per-module filtering (Batches 2–5):
#   - build_key() folds a section into the key, and builds byte-identical
#     keys for unscoped callers (existing cache entries keep working).
#   - @cached serves scoped viewers their own entry, never each other's,
#     and keeps the legacy key for unscoped callers.
#   - get_current_user returns the section scope from the sibling RPC, and a
#     scope-lookup failure is a loud retryable 503 — never silent "unscoped".
#   - PATCH /api/admin/users/{id}/scope sets/clears permissions.scope with
#     the same self/super_admin guard as the other admin user actions, and
#     drops the auth cache so it bites on the next request.

import pytest
from types import SimpleNamespace
from fastapi import HTTPException

import app.auth as auth_mod
import app.cache as cache_mod
import app.routers.admin as admin_mod


# ─── build_key ─────────────────────────────────────────────────────────────

def test_build_key_unscoped_is_byte_identical_to_legacy():
    assert cache_mod.build_key("employees") == "cache:employees:_all"
    assert cache_mod.build_key("x", status="a", priority="b") == "cache:x:priority=b&status=a"
    # Explicit None must equal omitting scope altogether.
    assert cache_mod.build_key("x", scope=None, status="a") == cache_mod.build_key("x", status="a")


def test_build_key_scoped_gets_own_segment():
    scoped = cache_mod.build_key("employees", scope="Projects")
    unscoped = cache_mod.build_key("employees")
    assert scoped == "cache:employees:scope=Projects"
    assert scoped != unscoped
    assert cache_mod.build_key("employees", scope="Projects") != cache_mod.build_key("employees", scope="Mining")
    # Scope composes with ordinary params rather than replacing them.
    assert cache_mod.build_key("x", scope="Projects", status="a") == "cache:x:status=a&scope=Projects"


# ─── @cached ────────────────────────────────────────────────────────────────

class _FakeRedis:
    """Minimal async Redis: plain dicts, no server, no timing flakes."""
    def __init__(self):
        self.kv = {}
        self.sets = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, payload, ex=None):
        self.kv[key] = payload

    async def sadd(self, keyset, key):
        self.sets.setdefault(keyset, set()).add(key)
        return 1

    async def smembers(self, keyset):
        return set(self.sets.get(keyset, set()))

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if self.kv.pop(k, None) is not None:
                n += 1
            if self.sets.pop(k, None) is not None:
                n += 1
        return n


@pytest.fixture
def fake_redis(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(cache_mod, "redis_client", redis)
    return redis


@pytest.mark.asyncio
async def test_cached_separates_scopes_and_keeps_legacy_key(fake_redis):
    calls = []

    @cache_mod.cached("scope_probe", ttl=60)
    async def probe(current_user=None):
        calls.append((current_user or {}).get("scope"))
        return {"served": (current_user or {}).get("scope")}

    projects = {"user_id": "u-1", "scope": "Projects"}
    mining = {"user_id": "u-2", "scope": "Mining"}

    assert await probe(current_user=projects) == {"served": "Projects"}
    assert await probe(current_user=projects) == {"served": "Projects"}  # cache hit
    assert await probe(current_user=mining) == {"served": "Mining"}      # own entry, not Projects'
    assert await probe(current_user=mining) == {"served": "Mining"}
    assert await probe() == {"served": None}                             # legacy unscoped key
    assert await probe() == {"served": None}

    assert calls == ["Projects", "Mining", None]
    assert "cache:scope_probe:_fn=probe" in fake_redis.kv
    assert "cache:scope_probe:_fn=probe&scope=Projects" in fake_redis.kv
    assert "cache:scope_probe:_fn=probe&scope=Mining" in fake_redis.kv


@pytest.mark.asyncio
async def test_cached_memory_fallback_still_separates_scopes(monkeypatch):
    """Redis down → the in-memory fallback answers, still per scope."""
    class _Down:
        async def get(self, key):
            raise ConnectionError("redis is down")

        async def set(self, *a, **k):
            raise ConnectionError("redis is down")

        async def sadd(self, *a, **k):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(cache_mod, "redis_client", _Down())
    calls = []

    @cache_mod.cached("scope_probe_mem", ttl=60)
    async def probe(current_user=None):
        calls.append((current_user or {}).get("scope"))
        return {"served": (current_user or {}).get("scope")}

    assert await probe(current_user={"user_id": "u-1", "scope": "Projects"}) == {"served": "Projects"}
    assert await probe(current_user={"user_id": "u-2", "scope": "Mining"}) == {"served": "Mining"}
    assert await probe(current_user={"user_id": "u-1", "scope": "Projects"}) == {"served": "Projects"}
    assert calls == ["Projects", "Mining"]


# ─── get_current_user scope ─────────────────────────────────────────────────

def _fake_user():
    return SimpleNamespace(
        id="user-1",
        email="takudzwa.chibi@example.com",
        user_metadata={},
        factors=[],  # no verified factor → MFA gate passes without aal2
    )


class _FakeAuthApi:
    def __init__(self, user):
        self._user = user

    def get_user(self, token):
        assert token == "good-token"
        return SimpleNamespace(user=self._user)


class _FakeRpcCall:
    def __init__(self, data=None, exc=None):
        self._data = data
        self._exc = exc

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(data=self._data)


class _FakeAuthSupabase:
    """Stands in for app.auth.supabase: token check + role/scope RPCs."""
    def __init__(self, role="user", scope=None, scope_exc=None):
        self.auth = _FakeAuthApi(_fake_user())
        self._role = role
        self._scope = scope
        self._scope_exc = scope_exc
        self.rpc_calls = []

    def rpc(self, fn, params):
        self.rpc_calls.append(fn)
        if fn == "get_user_role_by_id":
            return _FakeRpcCall(data=self._role)
        if fn == "get_user_scope_by_id":
            return _FakeRpcCall(data=self._scope, exc=self._scope_exc)
        raise AssertionError(f"unexpected RPC {fn}")


@pytest.mark.asyncio
async def test_auth_returns_scope_from_rpc(monkeypatch):
    fake = _FakeAuthSupabase(role="user", scope="Projects")
    monkeypatch.setattr(auth_mod, "supabase", fake)
    user = await auth_mod.get_current_user("Bearer good-token")
    assert user["scope"] == "Projects"
    assert user["role"] == "user"
    assert user["name"] == "Takudzwa Chibi"
    assert fake.rpc_calls == ["get_user_role_by_id", "get_user_scope_by_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, "", "   "])
async def test_auth_empty_scope_means_unscoped(monkeypatch, data):
    fake = _FakeAuthSupabase(role="manager", scope=data)
    monkeypatch.setattr(auth_mod, "supabase", fake)
    user = await auth_mod.get_current_user("Bearer good-token")
    assert user["scope"] is None
    assert user["role"] == "manager"


@pytest.mark.asyncio
async def test_auth_scope_lookup_failure_is_loud_503(monkeypatch):
    """A scope RPC failure — including the function missing before the Batch 1
    migration is applied — must never degrade to silent "unscoped"."""
    fake = _FakeAuthSupabase(scope_exc=Exception("function public.get_user_scope_by_id does not exist"))
    monkeypatch.setattr(auth_mod, "supabase", fake)
    with pytest.raises(HTTPException) as exc_info:
        await auth_mod.get_current_user("Bearer good-token")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_auth_scope_is_cached_with_user(monkeypatch):
    fake = _FakeAuthSupabase(scope="Projects")
    monkeypatch.setattr(auth_mod, "supabase", fake)
    first = await auth_mod.get_current_user("Bearer good-token")
    # Break the backend: a cache hit must still serve the scoped user.
    monkeypatch.setattr(auth_mod, "supabase", _FakeAuthSupabase(scope_exc=Exception("down")))
    second = await auth_mod.get_current_user("Bearer good-token")
    assert first == second
    assert second["scope"] == "Projects"


# ─── PATCH /users/{id}/scope ───────────────────────────────────────────────

class _FakeAdminTable:
    def __init__(self, state):
        self._state = state
        self._pending = None

    def select(self, *a, **k):
        return self

    def update(self, data):
        self._pending = data
        self._state["last_update"] = data
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        rows = self._state["profiles"]
        if self._pending is None:
            return SimpleNamespace(data=[dict(r) for r in rows])
        row = dict(rows[0]) if rows else {}
        row.update(self._pending)
        if rows:
            rows[0] = row
        return SimpleNamespace(data=[row] if rows else [])


class _FakeAdminSupabase:
    def __init__(self, state):
        self._state = state

    def table(self, name):
        assert name == "user_profiles"
        return _FakeAdminTable(self._state)


def _admin_state(*profiles):
    return {"profiles": [dict(p) for p in profiles]}


@pytest.mark.asyncio
async def test_set_scope_merges_into_permissions(monkeypatch):
    state = _admin_state({"id": "u-9", "role": "user", "email": "t@example.com",
                          "permissions": {"theme": "dark"}})
    monkeypatch.setattr(admin_mod, "supabase", _FakeAdminSupabase(state))
    caller = {"user_id": "admin-1", "role": "admin"}
    row = await admin_mod.set_user_scope("u-9", admin_mod.SetScopeRequest(scope="Projects"), caller)
    assert row["permissions"] == {"theme": "dark", "scope": "Projects"}


@pytest.mark.asyncio
async def test_clear_scope_removes_key_and_keeps_rest(monkeypatch):
    state = _admin_state({"id": "u-9", "role": "user", "email": "t@example.com",
                          "permissions": {"theme": "dark", "scope": "Projects"}})
    monkeypatch.setattr(admin_mod, "supabase", _FakeAdminSupabase(state))
    caller = {"user_id": "admin-1", "role": "admin"}
    row = await admin_mod.set_user_scope("u-9", admin_mod.SetScopeRequest(scope=None), caller)
    assert row["permissions"] == {"theme": "dark"}


@pytest.mark.asyncio
async def test_blank_scope_clears_and_long_scope_rejected(monkeypatch):
    state = _admin_state({"id": "u-9", "role": "user", "email": "t@example.com",
                          "permissions": {"scope": "Projects"}})
    monkeypatch.setattr(admin_mod, "supabase", _FakeAdminSupabase(state))
    caller = {"user_id": "admin-1", "role": "admin"}
    row = await admin_mod.set_user_scope("u-9", admin_mod.SetScopeRequest(scope="  "), caller)
    assert "scope" not in row["permissions"]
    with pytest.raises(HTTPException) as exc_info:
        await admin_mod.set_user_scope("u-9", admin_mod.SetScopeRequest(scope="x" * 65), caller)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_set_scope_guard_matches_other_admin_actions(monkeypatch):
    state = _admin_state({"id": "admin-1", "role": "admin", "email": "a@example.com", "permissions": {}})
    monkeypatch.setattr(admin_mod, "supabase", _FakeAdminSupabase(state))
    caller = {"user_id": "admin-1", "role": "admin"}
    with pytest.raises(HTTPException) as exc_info:
        await admin_mod.set_user_scope("admin-1", admin_mod.SetScopeRequest(scope="Projects"), caller)
    assert exc_info.value.status_code == 403

    state2 = _admin_state({"id": "u-s", "role": "super_admin", "email": "s@example.com", "permissions": {}})
    monkeypatch.setattr(admin_mod, "supabase", _FakeAdminSupabase(state2))
    with pytest.raises(HTTPException) as exc_info:
        await admin_mod.set_user_scope("u-s", admin_mod.SetScopeRequest(scope="Projects"), caller)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_set_scope_missing_user_is_404(monkeypatch):
    monkeypatch.setattr(admin_mod, "supabase", _FakeAdminSupabase(_admin_state()))
    with pytest.raises(HTTPException) as exc_info:
        await admin_mod.set_user_scope("nobody", admin_mod.SetScopeRequest(scope="Projects"),
                                       {"user_id": "admin-1", "role": "admin"})
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_set_scope_drops_auth_cache(monkeypatch):
    state = _admin_state({"id": "u-9", "role": "user", "email": "t@example.com", "permissions": {}})
    monkeypatch.setattr(admin_mod, "supabase", _FakeAdminSupabase(state))
    auth_mod._auth_cache["tokhash"] = (9999999999.0, {"user_id": "u-9", "scope": None}, True)
    await admin_mod.set_user_scope("u-9", admin_mod.SetScopeRequest(scope="Projects"),
                                   {"user_id": "admin-1", "role": "admin"})
    assert "tokhash" not in auth_mod._auth_cache
