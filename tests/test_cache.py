# tests/test_cache.py — the Redis response-cache layer (app/cache.py). Uses an
# in-memory fake Redis so no real Redis is required, covering the behaviours that
# matter: cache hit avoids recomputation, invalidation forces a refresh, keys don't
# collide across filters or across endpoints sharing a namespace, and a Redis outage
# degrades to a miss rather than raising.

import pytest
from app import cache as cache_mod


class FakeRedis:
    def __init__(self, broken=False):
        self.store = {}
        self.sets = {}
        self.broken = broken

    async def get(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.broken:
            raise ConnectionError("redis down")
        self.store[key] = value

    async def sadd(self, key, *values):
        if self.broken:
            raise ConnectionError("redis down")
        self.sets.setdefault(key, set()).update(values)

    async def smembers(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        return self.sets.get(key, set())

    async def delete(self, *keys):
        if self.broken:
            raise ConnectionError("redis down")
        for k in keys:
            self.store.pop(k, None)
            self.sets.pop(k, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fr = FakeRedis()
    monkeypatch.setattr(cache_mod, "redis_client", fr)
    monkeypatch.setattr(cache_mod, "_redis_down_until", 0.0)
    return fr


def test_build_key_ignores_param_order_and_empties():
    a = cache_mod.build_key("wo", status="open", priority=None)
    b = cache_mod.build_key("wo", priority=None, status="open")
    c = cache_mod.build_key("wo", status="closed", priority=None)
    assert a == b          # order-independent
    assert a != c          # different filter -> different key
    # None / "" / "all" are dropped, so all three collapse to the same key.
    assert cache_mod.build_key("wo") == cache_mod.build_key("wo", status=None) == cache_mod.build_key("wo", status="all")


async def test_cached_decorator_serves_from_cache(fake_redis):
    calls = {"n": 0}

    @cache_mod.cached("employees", ttl=60)
    async def get_employees():
        calls["n"] += 1
        return ["alice", "bob"]

    first = await get_employees()
    second = await get_employees()
    assert first == second == ["alice", "bob"]
    assert calls["n"] == 1  # second call served from cache, handler not re-run


async def test_invalidate_forces_refresh(fake_redis):
    calls = {"n": 0}

    @cache_mod.cached("employees", ttl=60)
    async def get_employees():
        calls["n"] += 1
        return calls["n"]

    await get_employees()
    await cache_mod.invalidate_namespace("employees")
    await get_employees()
    assert calls["n"] == 2  # invalidation cleared the entry, handler ran again


async def test_two_endpoints_same_namespace_dont_collide(fake_redis):
    @cache_mod.cached("work_orders", ttl=60)
    async def stats():
        return {"total": 42}

    @cache_mod.cached("work_orders", ttl=60)
    async def listing():
        return ["wo1", "wo2"]

    assert await stats() == {"total": 42}
    assert await listing() == ["wo1", "wo2"]  # not clobbered by stats()'s cached value


async def test_redis_outage_falls_back_to_memory(monkeypatch):
    # Contract change (Item 3, Oct 2026): an outage used to mean "no caching at
    # all" (every call recomputed). Now the in-memory fallback serves instead —
    # still no raising, but the handler runs once, not twice.
    monkeypatch.setattr(cache_mod, "redis_client", FakeRedis(broken=True))
    calls = {"n": 0}

    @cache_mod.cached("employees", ttl=60)
    async def get_employees():
        calls["n"] += 1
        return ["x"]

    assert await get_employees() == ["x"]
    assert await get_employees() == ["x"]
    assert calls["n"] == 1  # second call served from the memory fallback
    assert cache_mod.cache_stats()["memory_hits"] == 1


async def test_invalidate_clears_the_memory_fallback_too(monkeypatch):
    monkeypatch.setattr(cache_mod, "redis_client", FakeRedis(broken=True))
    calls = {"n": 0}

    @cache_mod.cached("employees", ttl=60)
    async def get_employees():
        calls["n"] += 1
        return calls["n"]

    assert await get_employees() == 1
    assert await get_employees() == 1  # memory hit
    await cache_mod.invalidate_namespace("employees")
    assert await get_employees() == 2  # cleared: recomputed
    assert cache_mod.cache_stats()["memory_keys"] == 1  # only the fresh entry


async def test_memory_entries_expire_by_ttl(monkeypatch):
    monkeypatch.setattr(cache_mod, "redis_client", FakeRedis(broken=True))
    await cache_mod.cache_set("k", {"v": 1}, ttl=0, namespace="ns")
    assert await cache_mod.cache_get("k") is None
    assert cache_mod.cache_stats()["memory_keys"] == 0  # dropped on sight, not kept


async def test_memory_is_not_consulted_while_redis_answers(fake_redis):
    # Redis is the single source of truth while up: a Redis miss is a miss
    # even when the fallback holds the key (e.g. Redis was flushed).
    await cache_mod.cache_set("k", {"v": 1}, ttl=60, namespace="ns")
    fake_redis.store.pop("k")
    assert await cache_mod.cache_get("k") is None
    assert cache_mod.cache_stats()["misses"] == 1
    assert cache_mod.cache_stats()["memory_hits"] == 0


async def test_memory_store_is_bounded(monkeypatch):
    monkeypatch.setattr(cache_mod, "redis_client", FakeRedis(broken=True))
    monkeypatch.setattr(cache_mod, "_MEMORY_MAX_KEYS", 2)
    await cache_mod.cache_set("a", 1, ttl=60, namespace="ns")
    await cache_mod.cache_set("b", 2, ttl=60, namespace="ns")
    await cache_mod.cache_set("c", 3, ttl=60, namespace="ns")
    assert cache_mod.cache_stats()["memory_keys"] == 2
    assert await cache_mod.cache_get("a") is None  # oldest-expiring evicted
    assert await cache_mod.cache_get("c") == 3


async def test_stats_count_hits_misses_sets_and_outages(fake_redis):
    await cache_mod.cache_set("k", [1], ttl=60, namespace="ns")
    assert await cache_mod.cache_get("k") == [1]
    assert await cache_mod.cache_get("missing") is None
    stats = cache_mod.cache_stats()
    assert (stats["sets"], stats["redis_hits"], stats["misses"]) == (1, 1, 1)
    assert stats["memory_hits"] == 0 and stats["redis_outages"] == 0
    assert stats["redis_in_cooldown"] is False
