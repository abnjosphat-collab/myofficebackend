# tests/test_overtime_hours_optional.py — the "pressed for time" fast path: reason/
# contact/exact times are no longer mandatory; an entry can carry `hours` directly
# instead of start/end times, but must provide ONE of the two duration sources.

import pytest
from pydantic import ValidationError

from app.routers.overtime import OvertimeCreate

BASE = dict(employee_name="A", employee_id="C1", position="Fitter", overtime_type="regular", date="2024-01-01")


def test_hours_only_is_valid():
    o = OvertimeCreate(**BASE, hours=3.5)
    assert o.hours == 3.5
    assert o.start_time is None
    assert o.reason is None  # no longer required


def test_times_only_is_valid_without_hours():
    o = OvertimeCreate(**BASE, start_time="17:00", end_time="19:00")
    assert o.hours is None
    assert o.start_time == "17:00" and o.end_time == "19:00"


def test_neither_hours_nor_times_is_rejected():
    with pytest.raises(ValidationError):
        OvertimeCreate(**BASE)


def test_reason_and_contact_are_optional():
    # Must not raise even though reason/contact_number are omitted entirely.
    o = OvertimeCreate(**BASE, hours=2)
    assert o.reason is None
    assert o.contact_number is None


@pytest.mark.parametrize("hours", [0, -1, 25])
def test_hours_out_of_range_rejected(hours):
    with pytest.raises(ValidationError):
        OvertimeCreate(**BASE, hours=hours)
