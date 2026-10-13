# tests/test_compressors_routine.py — the weekly routine each register compressor must
# have: ensure_weekly_routine_schedules creates the missing maintenance_schedules rows
# (one per compressor, weekly, Engineering-owned) so schedule generation raises their
# work orders. Idempotent, offline-aware, and quiet when the register is empty.
from datetime import date

import pytest

from app.compressor_register import EQUIPMENT_TABLE
from app.routers.compressors import (
    COMPRESSORS_TABLE,
    SCHEDULES_TABLE,
    ensure_weekly_routine_schedules,
    routine_marker,
)
from tests._compressors_fake import FakeSupabase

TUESDAY = date(2026, 10, 13)  # next Monday is 2026-10-19


def _unit(**overrides):
    base = {
        "id": 7, "equipment_id": "CMP 03", "name": "CMP 03", "model": "Atlas GA75",
        "location": "Compressor house", "category": "Compressors", "serial_number": "S-1",
    }
    base.update(overrides)
    return base


def _schedules(fake):
    return fake.state.tables.get(SCHEDULES_TABLE, [])


def test_creates_one_weekly_engineering_routine_per_register_compressor():
    fake = FakeSupabase({EQUIPMENT_TABLE: [_unit(), _unit(id=8, equipment_id="CMP 04", name="CMP 04")]})
    result = ensure_weekly_routine_schedules(fake, today=TUESDAY)
    assert result["checked"] == 2
    assert [c["equipment_id"] for c in result["created"]] == [7, 8]
    assert result["skipped"] == []

    rows = _schedules(fake)
    assert len(rows) == 2
    first = next(r for r in rows if r["equipment_info"] == routine_marker(7))
    assert first["name"] == "Weekly routine — CMP 03"
    assert first["to_department"] == "Engineering"
    assert first["recurrence_type"] == "weekly"
    assert first["active"] is True
    assert first["next_due_date"] == "2026-10-19"
    assert "CMP 03" in first["job_request_details"]


def test_second_run_creates_nothing_each_marker_exists_once():
    fake = FakeSupabase({EQUIPMENT_TABLE: [_unit()]})
    ensure_weekly_routine_schedules(fake, today=TUESDAY)
    result = ensure_weekly_routine_schedules(fake, today=TUESDAY)
    assert result["created"] == []
    assert result["skipped"] == [7]
    assert len(_schedules(fake)) == 1


def test_an_inactive_routine_is_a_pause_not_a_gap():
    # A manager who deactivates a routine schedule must not have it recreated behind them.
    fake = FakeSupabase({
        EQUIPMENT_TABLE: [_unit()],
        SCHEDULES_TABLE: [{"equipment_info": routine_marker(7), "recurrence_type": "weekly", "active": False}],
    })
    result = ensure_weekly_routine_schedules(fake, today=TUESDAY)
    assert result["created"] == []
    assert result["skipped"] == [7]


def test_offline_compressors_get_no_routine():
    fake = FakeSupabase({
        EQUIPMENT_TABLE: [_unit(), _unit(id=8, equipment_id="CMP 04", name="CMP 04")],
        COMPRESSORS_TABLE: [{"equipment_id": 8, "status": "offline"}],
    })
    result = ensure_weekly_routine_schedules(fake, today=TUESDAY)
    assert [c["equipment_id"] for c in result["created"]] == [7]
    assert result["skipped"] == [8]


def test_empty_register_is_a_quiet_noop():
    fake = FakeSupabase({})
    assert ensure_weekly_routine_schedules(fake, today=TUESDAY) == {"checked": 0, "created": [], "skipped": []}


def test_marker_is_stable_and_namespaced():
    assert routine_marker(7) == "compressor-routine:7"
