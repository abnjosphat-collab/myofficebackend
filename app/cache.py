# backend/app/cache.py — response caching on top of app/redis_client.py.
#
# redis_client.py already existed (connection + startup ping) but nothing
# ever actually read/wrote through it. This adds that layer: cache GET
# responses for the read-heavy, rarely-changing endpoints the homepage hits
# on every load (employees, equipment, work-order stats, breakdown overview),
# and invalidate the relevant cache entries whenever the corresponding
# resource is written.
#
# Deliberately best-effort: if Redis is down or unreachable, every function
# here fails soft instead of raising, so a cache outage degrades to "slower"
# rather than "broken" — consistent with how redis_client.ping_redis()
# already treats Redis as optional at startup. While Redis is unreachable,
# reads fall back to a bounded per-process memory store (written on every
# cache_set, cleared by the same invalidations), so a machine with no Redis
# at all still gets real caching with zero infrastructure.

import json
import logging
import time
from functools import wraps
from typing import Any, Awaitable, Optional, cast

from app.redis_client import redis_client
from app.scope import cache_scope_segment

logger = logging.getLogger(__name__)

KEY_PREFIX = "cache"

# Circuit breaker. Failing soft is right, but without this every cache call
# still *attempted* the connection and paid the full connect timeout — on a
# machine with no Redis that stacked into multi-second responses on every
# request. After a failure, skip Redis entirely for a cooldown; one cheap
# retry when it expires re-detects a recovered Redis.
_REDIS_COOLDOWN_SECONDS = 30.0
_redis_down_until = 0.0

# In-memory fallback. Strictly second choice: it is only *read* while Redis
# is in cooldown; while Redis answers, Redis is the single source of truth.
# Writes always go to both (when Redis accepts them) so a later outage finds
# warm entries, and invalidation always clears both. Per-process by nature —
# with several workers each holds its own copy, stale at most until its TTL.
_MEMORY_MAX_KEYS = 1000
_memory: dict[str, tuple[float, str, str]] = {}  # key -> (expires_at, json, namespace)

_stats = {
    "redis_hits": 0, "memory_hits": 0, "misses": 0,
    "sets": 0, "invalidations": 0, "redis_outages": 0,
}


def cache_stats() -> dict[str, Any]:
    """Lifetime cache counters for /api/health and debugging.

    Returns:
        Counters plus the live memory-store size and whether Redis is
        currently in cooldown. Counters reset only on process restart
        (or _reset_for_tests).
    """
    return {
        **_stats,
        "memory_keys": len(_memory),
        "redis_in_cooldown": not _redis_up(),
    }


def _reset_for_tests() -> None:
    """Clear the memory store, the breaker and the counters between tests."""
    global _redis_down_until
    _memory.clear()
    _redis_down_until = 0.0
    for key in _stats:
        _stats[key] = 0


def _redis_up() -> bool:
    return time.monotonic() >= _redis_down_until


def _mark_redis_down(op: str, e: Exception) -> None:
    global _redis_down_until
    _redis_down_until = time.monotonic() + _REDIS_COOLDOWN_SECONDS
    _stats["redis_outages"] += 1
    logger.warning(
        f"{op} failed ({e}); skipping Redis for {_REDIS_COOLDOWN_SECONDS:.0f}s"
    )


def _memory_hit(key: str) -> Optional[Any]:
    """Read the fallback store: a live entry, else None (expired entries are
    dropped on sight so they can't be served or counted again)."""
    entry = _memory.get(key)
    if entry is None:
        _stats["misses"] += 1
        return None
    expires_at, payload, _namespace = entry
    if expires_at <= time.monotonic():
        _memory.pop(key, None)
        _stats["misses"] += 1
        return None
    try:
        value = json.loads(payload)
    except Exception:
        _stats["misses"] += 1
        return None
    _stats["memory_hits"] += 1
    return value


def _memory_store(key: str, payload: str, ttl: int, namespace: str) -> None:
    """Write the fallback store, bounded: expired entries go first, then the
    oldest-expiring live entry if still full."""
    now = time.monotonic()
    if len(_memory) >= _MEMORY_MAX_KEYS and key not in _memory:
        for candidate, (expires_at, _payload, _ns) in list(_memory.items()):
            if expires_at <= now:
                _memory.pop(candidate, None)
        if len(_memory) >= _MEMORY_MAX_KEYS:
            oldest = min(_memory, key=lambda k: _memory[k][0])
            _memory.pop(oldest, None)
    _memory[key] = (now + ttl, payload, namespace)


def _namespace_keyset(namespace: str) -> str:
    """The Redis SET that tracks every cache key ever written under a
    namespace, so invalidation can delete them all without a SCAN."""
    return f"{KEY_PREFIX}:{namespace}:__keys__"


def build_key(namespace: str, scope: Any = None, **params: Any) -> str:
    """A stable cache key for a namespace + a set of query params (order-
    independent, so `status=a&priority=b` and `priority=b&status=a` share a
    cache entry). Params with a None/empty value are dropped so the
    unfiltered call (the common case) gets its own clean key.

    Scoped callers pass app/scope.py's viewer_scope(current_user) and get
    their own key segment, so a cached slice can never be served across
    scopes: sections keep their exact old segment, departments key as
    kind:value ("department:Mining"). Unscoped callers (scope None) build
    byte-identical keys to before scoping existed."""
    parts = sorted(f"{k}={v}" for k, v in params.items() if v not in (None, "", "all"))
    if scope:
        if isinstance(scope, dict):
            parts.append(f"scope={scope.get('kind')}:{scope.get('value')}")
        else:
            parts.append(f"scope={scope}")
    suffix = "&".join(parts) if parts else "_all"
    return f"{KEY_PREFIX}:{namespace}:{suffix}"


async def cache_get(key: str) -> Optional[Any]:
    """Returns the cached value (JSON-decoded) for `key`, or None on a miss
    or a decode failure. Redis answers first while it is up; when it is in
    cooldown the in-memory fallback answers instead."""
    if not _redis_up():
        return _memory_hit(key)
    try:
        raw = await redis_client.get(key)
    except Exception as e:
        _mark_redis_down(f"cache_get({key})", e)
        return _memory_hit(key)
    if raw is None:
        _stats["misses"] += 1
        return None
    try:
        value = json.loads(raw)
    except Exception:
        _stats["misses"] += 1
        return None
    _stats["redis_hits"] += 1
    return value


async def cache_set(key: str, value: Any, ttl: int, namespace: str) -> None:
    """Caches `value` under `key` for `ttl` seconds, and records `key` in the
    namespace's keyset so invalidate_namespace() can find it later. Always
    warms the in-memory fallback too, so a later Redis outage finds entries
    instead of an empty store. Never raises."""
    try:
        payload = json.dumps(value, default=str)
    except Exception:
        logger.warning(f"cache_set({key}): value is not JSON-serializable; skipping")
        return
    _memory_store(key, payload, ttl, namespace)
    _stats["sets"] += 1
    if not _redis_up():
        return
    try:
        await redis_client.set(key, payload, ex=ttl)
        await cast(Awaitable[int], redis_client.sadd(_namespace_keyset(namespace), key))
    except Exception as e:
        _mark_redis_down(f"cache_set({key})", e)


async def invalidate_namespace(namespace: str) -> None:
    """Deletes every cache entry ever written under `namespace`. Call this
    from a resource's write endpoints (create/update/delete) so the next
    read after a write always sees fresh data instead of a stale cache hit.

    Deliberately NOT behind the circuit breaker: if Redis is flaky rather
    than fully down, skipping invalidation would leave stale entries being
    served until their TTL. Writes are rare compared to reads, so paying the
    (now short) connect timeout here buys correctness cheaply."""
    for key, (_expires_at, _payload, entry_namespace) in list(_memory.items()):
        if entry_namespace == namespace:
            _memory.pop(key, None)
    _stats["invalidations"] += 1
    keyset = _namespace_keyset(namespace)
    try:
        keys = await cast(Awaitable[set[str]], redis_client.smembers(keyset))
        if keys:
            await redis_client.delete(*keys)
        await redis_client.delete(keyset)
    except Exception as e:
        logger.warning(f"invalidate_namespace({namespace}) failed: {e}")


def cached(namespace: str, ttl: int):
    """
    Decorator for a no-argument (or all-optional-default) GET route handler:
    serves a cached response when available, otherwise calls the handler and
    caches its result. Only appropriate for endpoints whose response doesn't
    vary by caller except by section scope (no other per-user data, no un-cached query params) — see
    build_key()-based manual caching for endpoints with filter params.

    The cache key always folds in the wrapped function's name, so two
    different @cached endpoints sharing a namespace (e.g. a stats-summary
    endpoint and a list endpoint both cached under "work_orders") never
    collide on the same key — each function gets its own entry regardless
    of what else shares its namespace. The viewer's section scope
    (from a current_user kwarg, when the endpoint takes one) is folded in
    the same way.
    """
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            # A scoped viewer's response is a filtered slice, never the whole:
            # fold their section into the key. Anything unscoped (no
            # current_user, or scope None) builds the exact key it always did.
            viewer = kwargs.get("current_user")
            raw = viewer.get("scope") if isinstance(viewer, dict) else None
            key = build_key(namespace, scope=cache_scope_segment(raw), _fn=fn.__name__)
            hit = await cache_get(key)
            if hit is not None:
                return hit
            result = await fn(*args, **kwargs)
            await cache_set(key, result, ttl, namespace)
            return result
        return wrapper
    return decorator
