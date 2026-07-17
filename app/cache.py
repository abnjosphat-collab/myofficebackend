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
# here fails soft (returns None / no-ops) instead of raising, so a cache
# outage degrades to "slower" rather than "broken" — consistent with how
# redis_client.ping_redis() already treats Redis as optional at startup.

import json
import logging
from functools import wraps
from typing import Any, Optional

from app.redis_client import redis_client

logger = logging.getLogger(__name__)

KEY_PREFIX = "cache"


def _namespace_keyset(namespace: str) -> str:
    """The Redis SET that tracks every cache key ever written under a
    namespace, so invalidation can delete them all without a SCAN."""
    return f"{KEY_PREFIX}:{namespace}:__keys__"


def build_key(namespace: str, **params: Any) -> str:
    """A stable cache key for a namespace + a set of query params (order-
    independent, so `status=a&priority=b` and `priority=b&status=a` share a
    cache entry). Params with a None/empty value are dropped so the
    unfiltered call (the common case) gets its own clean key."""
    parts = sorted(f"{k}={v}" for k, v in params.items() if v not in (None, "", "all"))
    suffix = "&".join(parts) if parts else "_all"
    return f"{KEY_PREFIX}:{namespace}:{suffix}"


async def cache_get(key: str) -> Optional[Any]:
    """Returns the cached value (JSON-decoded) for `key`, or None on a miss,
    a decode failure, or if Redis is unreachable."""
    try:
        raw = await redis_client.get(key)
    except Exception as e:
        logger.warning(f"cache_get({key}) failed, treating as a miss: {e}")
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def cache_set(key: str, value: Any, ttl: int, namespace: str) -> None:
    """Caches `value` under `key` for `ttl` seconds, and records `key` in the
    namespace's keyset so invalidate_namespace() can find it later."""
    try:
        await redis_client.set(key, json.dumps(value, default=str), ex=ttl)
        await redis_client.sadd(_namespace_keyset(namespace), key)
    except Exception as e:
        logger.warning(f"cache_set({key}) failed, continuing without caching: {e}")


async def invalidate_namespace(namespace: str) -> None:
    """Deletes every cache entry ever written under `namespace`. Call this
    from a resource's write endpoints (create/update/delete) so the next
    read after a write always sees fresh data instead of a stale cache hit."""
    keyset = _namespace_keyset(namespace)
    try:
        keys = await redis_client.smembers(keyset)
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
    vary by caller (no per-user data, no un-cached query params) — see
    build_key()-based manual caching for endpoints with filter params.

    The cache key always folds in the wrapped function's name, so two
    different @cached endpoints sharing a namespace (e.g. a stats-summary
    endpoint and a list endpoint both cached under "work_orders") never
    collide on the same key — each function gets its own entry regardless
    of what else shares its namespace.
    """
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            key = build_key(namespace, _fn=fn.__name__)
            hit = await cache_get(key)
            if hit is not None:
                return hit
            result = await fn(*args, **kwargs)
            await cache_set(key, result, ttl, namespace)
            return result
        return wrapper
    return decorator
