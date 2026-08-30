# tests/test_compressors_performance_metrics.py — get_performance_metrics computes
# avg_efficiency (excluding invalid zero-efficiency readings), avg daily running/loaded
# hours, and downtime_percentage (days with zero running hours) per compressor - real
# reliability metrics with zero prior tests. Calls the coroutine directly, passing a
# fake client straight into the supabase_client parameter (bypassing the Depends()
# default entirely, since this isn't going through FastAPI's actual request pipeline).

import pytest

from app.routers.compressors import get_performance_metrics


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, response_data):
        self._response = response_data

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def execute(self): return _Resp(self._response)


class _FakeClient:
    def __init__(self, compressors, readings_by_compressor):
        self._compressors = compressors
        self._readings = readings_by_compressor

    def table(self, name):
        if name == "compressors":
            return _FakeQuery(self._compressors)
        # readings table — the fake ignores the .eq(compressor_id) filter and always
        # returns whichever compressor's readings the test set up (assumes one
        # compressor per test for simplicity, matching each test's fixture below)
        return _FakeQuery(self._readings)


async def test_no_compressors_is_an_empty_list():
    client = _FakeClient(compressors=[], readings_by_compressor=[])
    result = await get_performance_metrics(period_days=30, supabase_client=client)
    assert result == []


async def test_compressor_with_no_readings_gets_the_zero_fallback_shape():
    client = _FakeClient(
        compressors=[{"id": 1, "name": "Compressor A", "total_running_hours": 500, "total_loaded_hours": 300}],
        readings_by_compressor=[],
    )
    result = await get_performance_metrics(period_days=30, supabase_client=client)
    assert len(result) == 1
    assert result[0]["avg_efficiency"] == 0.0
    assert result[0]["total_running_hours"] == 500  # still reports lifetime totals from the compressor row


async def test_average_efficiency_excludes_zero_readings():
    client = _FakeClient(
        compressors=[{"id": 1, "name": "Compressor A", "total_running_hours": 100, "total_loaded_hours": 80}],
        readings_by_compressor=[
            {"daily_running_hours": 8, "daily_loaded_hours": 6, "efficiency": 75},
            {"daily_running_hours": 8, "daily_loaded_hours": 7, "efficiency": 85},
            {"daily_running_hours": 0, "daily_loaded_hours": 0, "efficiency": 0},  # invalid/no-data reading
        ],
    )
    result = await get_performance_metrics(period_days=30, supabase_client=client)
    # (75 + 85) / 2 -- the zero-efficiency reading is excluded, not averaged in as 0.
    assert result[0]["avg_efficiency"] == 80.0


async def test_downtime_percentage_counts_zero_running_days():
    client = _FakeClient(
        compressors=[{"id": 1, "name": "Compressor A", "total_running_hours": 100, "total_loaded_hours": 80}],
        readings_by_compressor=[
            {"daily_running_hours": 8, "daily_loaded_hours": 6, "efficiency": 75},
            {"daily_running_hours": 0, "daily_loaded_hours": 0, "efficiency": 0},
            {"daily_running_hours": 0, "daily_loaded_hours": 0, "efficiency": 0},
            {"daily_running_hours": 8, "daily_loaded_hours": 6, "efficiency": 80},
        ],
    )
    result = await get_performance_metrics(period_days=30, supabase_client=client)
    assert result[0]["downtime_percentage"] == 50.0  # 2 of 4 days had zero running hours
