# tests/test_employees_bulk_normalize.py — bulk roster normalization endpoint.

import pytest
from fastapi import HTTPException

import app.routers.employees as emp_mod
from app.routers.employees import BulkNormalizeRequest, BulkNormalizeItem, bulk_normalize_employees


class _BrokenRedis:
    async def get(self, *a, **k): raise ConnectionError("no redis in tests")
    async def set(self, *a, **k): raise ConnectionError("no redis in tests")
    async def sadd(self, *a, **k): raise ConnectionError("no redis in tests")
    async def smembers(self, *a, **k): raise ConnectionError("no redis in tests")
    async def delete(self, *a, **k): raise ConnectionError("no redis in tests")


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    from app import cache as cache_mod
    monkeypatch.setattr(cache_mod, "redis_client", _BrokenRedis())


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, calls, responses):
        self.table_name = table_name
        self._calls = calls
        self._responses = responses
        self._payload = None

    def update(self, data):
        self._payload = data
        return self

    def eq(self, col, val):
        self._calls.append({"op": "update", "table": self.table_name, "payload": self._payload, "id": val})
        return self

    def execute(self):
        if self._responses:
            return _Resp(self._responses.pop(0))
        return _Resp([])


class _FakeSupabase:
    def __init__(self, responses):
        self.calls = []
        self._responses = list(responses)

    def table(self, name):
        return _FakeQuery(name, self.calls, self._responses)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(responses):
        fake = _FakeSupabase(responses)
        monkeypatch.setattr(emp_mod, "supabase", fake)
        return fake
    return _patch


async def test_bulk_normalize_updates_each_record(patch_supabase):
    fake = patch_supabase([
        [{"id": 1, "designation": "Light Vehicle Driver"}],
        [{"id": 2, "designation": "Fitter Class 2"}],
    ])
    body = BulkNormalizeRequest(updates=[
        BulkNormalizeItem(id=1, designation="Light Vehicle Driver"),
        BulkNormalizeItem(id=2, designation="Fitter Class 2", section="Mechanical"),
    ])
    result = await bulk_normalize_employees(body)
    assert result.succeeded == 2
    assert result.failed == 0
    assert len(fake.calls) == 2
    assert fake.calls[1]["payload"]["section"] == "Mechanical"


async def test_bulk_normalize_counts_missing_rows(patch_supabase):
    patch_supabase([[]])
    body = BulkNormalizeRequest(updates=[
        BulkNormalizeItem(id=99, designation="Foreman"),
    ])
    result = await bulk_normalize_employees(body)
    assert result.succeeded == 0
    assert result.failed == 1
    assert "not found" in result.errors[0]
