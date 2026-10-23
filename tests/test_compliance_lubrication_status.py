# tests/test_compliance_lubrication_status.py — compliance.py's _compute_status
# (overdue/due_soon/current on a 30-day window) and lubrication.py's _lube_status
# (same shape, but a 7-day window) had zero tests. Worth testing both explicitly
# since they're easy to confuse with each other (same shape, different thresholds) or
# with the several other expiry-status functions covered elsewhere this pass
# (compressors' 7/30-day service urgency, training's 90-day cert expiry).

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import app.clock as clock_mod
from app.clock import today_harare
from app.routers.compliance import _compute_status
from app.routers.lubrication import _lube_status

# 23:30 UTC is already the next day in Harare (UTC+2): the audit's F-12
# scenario. The frozen date is deliberately far from any run date so a UTC
# "today" fails these tests deterministically (until 2030).
FROZEN_HARARE = datetime(2030, 5, 6, 1, 30, tzinfo=ZoneInfo("Africa/Harare"))


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(clock_mod, "now_harare", lambda: FROZEN_HARARE)



def _iso(days_from_today: int) -> str:
    return (today_harare() + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


# ─── compliance._compute_status — 30-day window ─────────────────────────────────────

def test_compliance_no_expiry_is_current():
    assert _compute_status(None) == "current"


def test_compliance_overdue():
    assert _compute_status(_iso(-1)) == "overdue"


def test_compliance_exactly_30_days_out_is_due_soon():
    assert _compute_status(_iso(30)) == "due_soon"


def test_compliance_31_days_out_is_current():
    assert _compute_status(_iso(31)) == "current"


def test_compliance_malformed_date_is_unknown_not_a_crash():
    assert _compute_status("not-a-date") == "unknown"


# ─── lubrication._lube_status — 7-day window (narrower than compliance's 30) ───────

def test_lube_no_due_date_is_current():
    assert _lube_status(None) == "current"


def test_lube_overdue():
    assert _lube_status(_iso(-1)) == "overdue"


def test_lube_exactly_7_days_out_is_due_soon():
    assert _lube_status(_iso(7)) == "due_soon"


def test_lube_8_days_out_is_current():
    # Confirms lubrication's window is 7 days, not accidentally reusing compliance's 30.
    assert _lube_status(_iso(8)) == "current"


def test_lube_malformed_date_is_unknown_not_a_crash():
    assert _lube_status("garbage") == "unknown"

def test_compliance_uses_the_harare_date_near_midnight_utc(frozen_clock):
    # At 23:30 UTC Harare is already on 2030-05-06: an expiry on that day
    # is "due_soon", not "current".
    assert _compute_status("2030-05-06") == "due_soon"


def test_lube_uses_the_harare_date_near_midnight_utc(frozen_clock):
    assert _lube_status("2030-05-06") == "due_soon"
