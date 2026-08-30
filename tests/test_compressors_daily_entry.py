# tests/test_compressors_daily_entry.py — create_daily_entry_cumulative, the
# largest and most business-logic-heavy handler in compressors.py (recomputes
# daily hours from cumulative meter totals, validates monotonicity, retries on
# a specific DB check-constraint failure, conditionally updates the compressor's
# running totals, and triggers check_and_update_service_due) — plus
# check_and_update_service_due and recalculate_subsequent_readings directly.
# All three had zero coverage before this file.

import pytest
from fastapi import HTTPException

from app.routers.compressors import (
    create_daily_entry_cumulative, check_and_update_service_due,
    recalculate_subsequent_readings, calculate_efficiency as calculate_efficiency_helper,
    DailyUpdateRequest, COMPRESSORS_TABLE, READINGS_TABLE,
    MAINTENANCE_SCHEDULE_TABLE, ALERTS_TABLE, SERVICE_INTERVALS_TABLE,
)
from tests._compressors_fake import FakeSupabase


def _compressor(**overrides):
    base = {
        "id": "c1", "name": "Compressor A", "initial_total_running": 0.0,
        "initial_total_loaded": 0.0, "total_running_hours": 0.0, "total_loaded_hours": 0.0,
    }
    base.update(overrides)
    return base


def _reading(**overrides):
    base = {
        "id": "r1", "compressor_id": "c1", "date": "2024-01-01",
        "total_running_hours": 100.0, "total_loaded_hours": 80.0,
        "daily_running_hours": 8.0, "daily_loaded_hours": 6.0, "efficiency": 75.0,
        "created_at": "2024-01-01T00:00:00",
    }
    base.update(overrides)
    return base


# ─── create_daily_entry_cumulative: validation ───────────────────────────────────────

async def test_invalid_date_format_is_400():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor()]})
    req = DailyUpdateRequest(compressor_id="c1", date="01/15/2024", current_total_running=10, current_total_loaded=8)
    with pytest.raises(HTTPException) as exc:
        await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert exc.value.status_code == 400


async def test_unknown_compressor_is_404():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    req = DailyUpdateRequest(compressor_id="ghost", date="2024-01-15", current_total_running=10, current_total_loaded=8)
    with pytest.raises(HTTPException) as exc:
        await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert exc.value.status_code == 404


# ─── first reading (no previous reading — falls back to compressor's initial totals) ─

async def test_first_reading_uses_compressor_initial_totals():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(initial_total_running=100.0, initial_total_loaded=80.0)],
        READINGS_TABLE: [],
    })
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=108.0, current_total_loaded=86.0)
    result = await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert result["success"] is True
    assert result["data"]["daily_running_hours"] == 8.0
    assert result["data"]["daily_loaded_hours"] == 6.0
    assert result["data"]["efficiency"] == 75.0
    # it's the only/most-recent reading -> compressor totals get updated
    updated = fake.state.tables[COMPRESSORS_TABLE][0]
    assert updated["total_running_hours"] == 108.0
    assert updated["total_loaded_hours"] == 86.0


async def test_first_reading_below_initial_running_is_400():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(initial_total_running=100.0)], READINGS_TABLE: []})
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=50.0, current_total_loaded=0.0)
    with pytest.raises(HTTPException) as exc:
        await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert exc.value.status_code == 400
    assert "cannot be less than initial total" in exc.value.detail


async def test_first_reading_below_initial_loaded_is_400():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor(initial_total_running=100.0, initial_total_loaded=80.0)], READINGS_TABLE: []})
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=110.0, current_total_loaded=50.0)
    with pytest.raises(HTTPException) as exc:
        await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert exc.value.status_code == 400
    assert "loaded hours" in exc.value.detail


# ─── subsequent reading (a previous reading exists) ──────────────────────────────────

async def test_subsequent_reading_computes_diff_against_previous_reading():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(total_running_hours=100.0, total_loaded_hours=80.0)],
        READINGS_TABLE: [_reading(date="2024-01-14", total_running_hours=100.0, total_loaded_hours=80.0)],
    })
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=112.0, current_total_loaded=87.0)
    result = await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert result["data"]["daily_running_hours"] == 12.0
    assert result["data"]["daily_loaded_hours"] == 7.0


async def test_current_running_below_previous_reading_is_400():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor()],
        READINGS_TABLE: [_reading(date="2024-01-14", total_running_hours=100.0, total_loaded_hours=80.0)],
    })
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=90.0, current_total_loaded=85.0)
    with pytest.raises(HTTPException) as exc:
        await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert exc.value.status_code == 400
    assert "previous reading's total" in exc.value.detail


async def test_current_loaded_below_previous_reading_is_400():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor()],
        READINGS_TABLE: [_reading(date="2024-01-14", total_running_hours=100.0, total_loaded_hours=80.0)],
    })
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=110.0, current_total_loaded=70.0)
    with pytest.raises(HTTPException) as exc:
        await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert exc.value.status_code == 400
    assert "previous reading's total" in exc.value.detail


async def test_loaded_hours_exceeding_running_hours_gets_clamped():
    # A bad/estimated reading claims more loaded hours than running hours for the day —
    # ensure_valid_daily_hours must clamp loaded down to running and recompute the total.
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor()],
        READINGS_TABLE: [_reading(date="2024-01-14", total_running_hours=100.0, total_loaded_hours=80.0)],
    })
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=105.0, current_total_loaded=95.0)
    result = await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    # daily_running = 5, daily_loaded would be 15 -> clamped to 5
    assert result["data"]["daily_running_hours"] == 5.0
    assert result["data"]["daily_loaded_hours"] == 5.0
    assert result["data"]["total_loaded_hours"] == 85.0  # previous (80) + clamped daily (5)


# ─── existing reading for the same date gets updated, not duplicated ────────────────

async def test_existing_reading_for_same_date_is_updated_not_duplicated():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(total_running_hours=108.0, total_loaded_hours=86.0)],
        READINGS_TABLE: [
            _reading(id="prev", date="2024-01-14", total_running_hours=100.0, total_loaded_hours=80.0),
            _reading(id="r-today", date="2024-01-15", total_running_hours=108.0, total_loaded_hours=86.0, created_at="2024-01-15T09:00:00"),
        ],
    })
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=110.0, current_total_loaded=87.0)
    result = await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert result["data"]["id"] == "r-today"
    assert result["data"]["created_at"] == "2024-01-15T09:00:00"  # preserved, not overwritten
    readings = fake.state.tables[READINGS_TABLE]
    assert len(readings) == 2  # no new row inserted
    assert [r for r in readings if r["id"] == "r-today"][0]["total_running_hours"] == 110.0


async def test_backdated_entry_does_not_overwrite_compressor_current_totals():
    # Posting an entry for a date that ISN'T the most recent reading must not clobber
    # the compressor's live totals with the (older) backdated values.
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(total_running_hours=200.0, total_loaded_hours=150.0)],
        READINGS_TABLE: [
            _reading(id="future", date="2024-01-20", total_running_hours=200.0, total_loaded_hours=150.0),
        ],
    })
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=120.0, current_total_loaded=90.0)
    await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    updated = fake.state.tables[COMPRESSORS_TABLE][0]
    # unchanged — the backdated 2024-01-15 entry isn't the latest reading (2024-01-20 is)
    assert updated["total_running_hours"] == 200.0
    assert updated["total_loaded_hours"] == 150.0


# ─── constraint-violation retry path ─────────────────────────────────────────────────

async def test_check_constraint_violation_retries_and_succeeds():
    # A day where the compressor ran but was never loaded (daily_loaded == 0 exactly) is
    # valid at the app-validation layer (ensure_valid_daily_hours only floors negatives
    # to zero — it does not forbid zero) but can still trip a stricter DB CHECK
    # constraint that requires daily_loaded_hours to be strictly positive. This is the
    # one legitimate way to reach the retry branch without also tripping the earlier
    # "current total can't be less than previous total" validation (which would fire
    # first, as a 400, for any input that produces a genuinely negative daily_loaded).
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(initial_total_running=100.0, initial_total_loaded=80.0)],
        READINGS_TABLE: [],
    })
    fake.fail_once(READINGS_TABLE, "insert",
                    'new row for relation "compressor_readings" violates check constraint "chk_daily_loaded_positive"')
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=108.0, current_total_loaded=80.0)
    result = await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert result["success"] is True
    assert result["data"]["daily_running_hours"] == 8.0
    assert result["data"]["daily_loaded_hours"] == 0
    assert result["data"]["total_loaded_hours"] == 80.0
    assert len(fake.state.tables[READINGS_TABLE]) == 1  # retry succeeded, exactly one row


async def test_check_constraint_violation_on_update_path_also_retries():
    # Same recovery path as the insert case above, but exercised via the UPDATE branch
    # (existing_reading truthy) rather than insert.
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(total_running_hours=108.0, total_loaded_hours=80.0)],
        READINGS_TABLE: [_reading(id="r-today", date="2024-01-15", total_running_hours=108.0, total_loaded_hours=80.0)],
    })
    fake.fail_once(READINGS_TABLE, "update",
                    'violates check constraint "chk_daily_loaded_positive"')
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=108.0, current_total_loaded=80.0)
    result = await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert result["success"] is True
    assert result["data"]["id"] == "r-today"
    assert len(fake.state.tables[READINGS_TABLE]) == 1  # retried in place, not duplicated


async def test_other_constraint_violation_is_400_not_retried():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor()], READINGS_TABLE: []})
    fake.fail_once(READINGS_TABLE, "insert", 'violates check constraint "chk_something_else"')
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=8.0, current_total_loaded=6.0)
    with pytest.raises(HTTPException) as exc:
        await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert exc.value.status_code == 400
    assert "Database constraint error" in exc.value.detail


async def test_unrelated_db_error_propagates_as_500():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor()], READINGS_TABLE: []})
    fake.fail_once(READINGS_TABLE, "insert", "connection reset by peer")
    req = DailyUpdateRequest(compressor_id="c1", date="2024-01-15", current_total_running=8.0, current_total_loaded=6.0)
    with pytest.raises(HTTPException) as exc:
        await create_daily_entry_cumulative(request=req, supabase_client=fake, current_user={})
    assert exc.value.status_code == 500


# ─── check_and_update_service_due ────────────────────────────────────────────────────

async def test_service_due_unknown_compressor_is_a_silent_noop():
    fake = FakeSupabase({COMPRESSORS_TABLE: []})
    await check_and_update_service_due("ghost", 500.0, fake)  # must not raise


async def test_service_due_no_intervals_configured_is_a_silent_noop():
    fake = FakeSupabase({COMPRESSORS_TABLE: [_compressor()], SERVICE_INTERVALS_TABLE: []})
    await check_and_update_service_due("c1", 500.0, fake)
    assert fake.state.tables.get(MAINTENANCE_SCHEDULE_TABLE, []) == []


async def test_service_due_creates_new_schedule_and_alert_when_critical():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(name="Compressor A")],
        SERVICE_INTERVALS_TABLE: [{"interval_hours": 1000}],
        MAINTENANCE_SCHEDULE_TABLE: [],
        ALERTS_TABLE: [],
    })
    await check_and_update_service_due("c1", 999.0, fake)  # 1 hour remaining -> critical
    schedules = fake.state.tables[MAINTENANCE_SCHEDULE_TABLE]
    assert len(schedules) == 1
    assert schedules[0]["urgency"] == "critical"
    alerts = fake.state.tables[ALERTS_TABLE]
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"


async def test_service_due_updates_existing_schedule_instead_of_duplicating():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(name="Compressor A")],
        SERVICE_INTERVALS_TABLE: [{"interval_hours": 1000}],
        MAINTENANCE_SCHEDULE_TABLE: [{
            "id": "sched1", "compressor_id": "c1", "service_interval_hours": 1000,
            "urgency": "low",
        }],
        ALERTS_TABLE: [],
    })
    await check_and_update_service_due("c1", 999.0, fake)
    schedules = fake.state.tables[MAINTENANCE_SCHEDULE_TABLE]
    assert len(schedules) == 1  # updated in place, not duplicated
    assert schedules[0]["id"] == "sched1"
    assert schedules[0]["urgency"] == "critical"


async def test_service_due_high_urgency_within_a_week():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(name="Compressor A")],
        SERVICE_INTERVALS_TABLE: [{"interval_hours": 1000}],
        MAINTENANCE_SCHEDULE_TABLE: [],
        ALERTS_TABLE: [],
    })
    await check_and_update_service_due("c1", 960.0, fake)  # 40h remaining -> 5 days -> high
    schedule = fake.state.tables[MAINTENANCE_SCHEDULE_TABLE][0]
    assert schedule["urgency"] == "high"
    assert len(fake.state.tables[ALERTS_TABLE]) == 1
    assert fake.state.tables[ALERTS_TABLE][0]["severity"] == "warning"


async def test_service_due_medium_urgency_within_a_month():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(name="Compressor A")],
        SERVICE_INTERVALS_TABLE: [{"interval_hours": 1000}],
        MAINTENANCE_SCHEDULE_TABLE: [],
        ALERTS_TABLE: [],
    })
    await check_and_update_service_due("c1", 900.0, fake)  # 100h remaining -> 12 days -> medium
    schedule = fake.state.tables[MAINTENANCE_SCHEDULE_TABLE][0]
    assert schedule["urgency"] == "medium"
    assert fake.state.tables[ALERTS_TABLE] == []  # medium doesn't trigger an alert


async def test_service_due_low_urgency_creates_no_alert():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(name="Compressor A")],
        SERVICE_INTERVALS_TABLE: [{"interval_hours": 1000}],
        MAINTENANCE_SCHEDULE_TABLE: [],
        ALERTS_TABLE: [],
    })
    await check_and_update_service_due("c1", 0.0, fake)  # 1000h remaining -> 125 days -> low
    assert fake.state.tables[MAINTENANCE_SCHEDULE_TABLE][0]["urgency"] == "low"
    assert fake.state.tables[ALERTS_TABLE] == []


async def test_service_due_already_past_every_interval_is_a_noop():
    fake = FakeSupabase({
        COMPRESSORS_TABLE: [_compressor(name="Compressor A")],
        SERVICE_INTERVALS_TABLE: [{"interval_hours": 1000}],
    })
    await check_and_update_service_due("c1", 5000.0, fake)  # past the only interval
    assert fake.state.tables.get(MAINTENANCE_SCHEDULE_TABLE, []) == []


async def test_service_due_swallows_exceptions_instead_of_raising():
    fake = FakeSupabase({})
    fake.always_fail(COMPRESSORS_TABLE, "boom")
    await check_and_update_service_due("c1", 500.0, fake)  # must not raise


# ─── recalculate_subsequent_readings ─────────────────────────────────────────────────

async def test_recalculate_no_subsequent_readings_is_a_noop():
    fake = FakeSupabase({READINGS_TABLE: []})
    await recalculate_subsequent_readings("c1", "2024-01-10", fake)  # must not raise


async def test_recalculate_updates_daily_hours_using_the_from_date_reading():
    fake = FakeSupabase({READINGS_TABLE: [
        _reading(id="anchor", date="2024-01-10", total_running_hours=100.0, total_loaded_hours=80.0),
        _reading(id="next", date="2024-01-11", total_running_hours=112.0, total_loaded_hours=88.0,
                  daily_running_hours=0, daily_loaded_hours=0, efficiency=0),
    ]})
    await recalculate_subsequent_readings("c1", "2024-01-10", fake)
    updated = [r for r in fake.state.tables[READINGS_TABLE] if r["id"] == "next"][0]
    assert updated["daily_running_hours"] == 12.0
    assert updated["daily_loaded_hours"] == 8.0
    assert updated["efficiency"] == calculate_efficiency_helper(12.0, 8.0)


async def test_recalculate_chains_across_multiple_subsequent_readings():
    fake = FakeSupabase({READINGS_TABLE: [
        _reading(id="anchor", date="2024-01-10", total_running_hours=100.0, total_loaded_hours=80.0),
        _reading(id="day1", date="2024-01-11", total_running_hours=108.0, total_loaded_hours=84.0,
                  daily_running_hours=0, daily_loaded_hours=0, efficiency=0),
        _reading(id="day2", date="2024-01-12", total_running_hours=120.0, total_loaded_hours=92.0,
                  daily_running_hours=0, daily_loaded_hours=0, efficiency=0),
    ]})
    await recalculate_subsequent_readings("c1", "2024-01-10", fake)
    rows = {r["id"]: r for r in fake.state.tables[READINGS_TABLE]}
    assert rows["day1"]["daily_running_hours"] == 8.0
    # day2 is diffed against day1's total (108/84), not the anchor
    assert rows["day2"]["daily_running_hours"] == 12.0
    assert rows["day2"]["daily_loaded_hours"] == 8.0


async def test_recalculate_falls_back_to_latest_reading_before_from_date_when_from_date_missing():
    fake = FakeSupabase({READINGS_TABLE: [
        _reading(id="older", date="2024-01-05", total_running_hours=90.0, total_loaded_hours=70.0),
        # no reading dated exactly 2024-01-10
        _reading(id="next", date="2024-01-11", total_running_hours=100.0, total_loaded_hours=80.0,
                  daily_running_hours=0, daily_loaded_hours=0, efficiency=0),
    ]})
    await recalculate_subsequent_readings("c1", "2024-01-10", fake)
    updated = [r for r in fake.state.tables[READINGS_TABLE] if r["id"] == "next"][0]
    assert updated["daily_running_hours"] == 10.0  # diffed against the 2024-01-05 fallback
    assert updated["daily_loaded_hours"] == 10.0


async def test_recalculate_skips_reading_with_no_predecessor_at_all():
    # The very first subsequent reading has no reading dated exactly from_date AND no
    # reading before from_date either -> nothing to diff against, so it's skipped
    # entirely (not updated, not errored).
    fake = FakeSupabase({READINGS_TABLE: [
        _reading(id="only", date="2024-01-11", total_running_hours=50.0, total_loaded_hours=40.0,
                  daily_running_hours=999, daily_loaded_hours=999, efficiency=999),
    ]})
    await recalculate_subsequent_readings("c1", "2024-01-10", fake)
    unchanged = fake.state.tables[READINGS_TABLE][0]
    assert unchanged["daily_running_hours"] == 999  # left untouched -- the `continue` fired


async def test_recalculate_clamps_loaded_hours_exceeding_running_hours():
    fake = FakeSupabase({READINGS_TABLE: [
        _reading(id="anchor", date="2024-01-10", total_running_hours=100.0, total_loaded_hours=80.0),
        _reading(id="next", date="2024-01-11", total_running_hours=105.0, total_loaded_hours=95.0,
                  daily_running_hours=0, daily_loaded_hours=0, efficiency=0),
    ]})
    await recalculate_subsequent_readings("c1", "2024-01-10", fake)
    updated = [r for r in fake.state.tables[READINGS_TABLE] if r["id"] == "next"][0]
    # daily_running = 5, daily_loaded would be 15 -> clamped down to 5
    assert updated["daily_running_hours"] == 5
    assert updated["daily_loaded_hours"] == 5


async def test_recalculate_swallows_exceptions_instead_of_raising():
    fake = FakeSupabase({})
    fake.always_fail(READINGS_TABLE, "boom")
    await recalculate_subsequent_readings("c1", "2024-01-10", fake)  # must not raise

