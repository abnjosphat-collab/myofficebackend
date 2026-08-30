# tests/test_redis_client.py — ping_redis/close_redis both swallow every exception to a
# safe fallback (False / silent) rather than raising, so a Redis outage never brings
# down a request — had zero direct tests confirming the swallow actually happens.

import pytest

import app.redis_client as redis_mod
from app.redis_client import ping_redis, close_redis


class _FakeRedisOK:
    async def ping(self):
        return True

    async def aclose(self):
        return None


class _FakeRedisDown:
    async def ping(self):
        raise ConnectionError("simulated Redis outage")

    async def aclose(self):
        raise ConnectionError("simulated Redis outage")


async def test_ping_returns_true_when_redis_is_reachable(monkeypatch):
    monkeypatch.setattr(redis_mod, "redis_client", _FakeRedisOK())
    assert await ping_redis() is True


async def test_ping_returns_false_instead_of_raising_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(redis_mod, "redis_client", _FakeRedisDown())
    assert await ping_redis() is False


async def test_close_redis_does_not_raise_when_redis_is_already_unreachable(monkeypatch):
    monkeypatch.setattr(redis_mod, "redis_client", _FakeRedisDown())
    await close_redis()  # must not raise


async def test_close_redis_succeeds_normally(monkeypatch):
    monkeypatch.setattr(redis_mod, "redis_client", _FakeRedisOK())
    await close_redis()  # must not raise
