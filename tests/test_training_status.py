# tests/test_training_status.py — check_status() classifies a certification's
# expiry into Expired/Due Soon/Valid, the compliance-status shown for every training
# record in the app. Zero prior tests despite being real compliance-relevant logic with
# a clear boundary (exactly 90 days out) worth locking in precisely. Dates are computed
# relative to today_harare() at test-run time, the same "today" the product uses.

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import app.clock as clock_mod
from app.clock import today_harare
from app.routers.training import check_status


# 23:30 UTC is already the next day in Harare (UTC+2): the audit's F-12
# scenario. The frozen date is deliberately far from any run date so a UTC
# "today" fails these tests deterministically (until 2030).
FROZEN_HARARE = datetime(2030, 5, 6, 1, 30, tzinfo=ZoneInfo("Africa/Harare"))


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(clock_mod, "now_harare", lambda: FROZEN_HARARE)


def test_past_expiry_is_expired():
    assert check_status(today_harare() - timedelta(days=1)) == "Expired"


def test_expiry_today_is_not_expired():
    # `< today`, not `<= today` — a certificate expiring today hasn't lapsed yet.
    assert check_status(today_harare()) == "Due Soon"


def test_expiry_exactly_90_days_out_is_due_soon():
    assert check_status(today_harare() + timedelta(days=90)) == "Due Soon"


def test_expiry_91_days_out_is_valid():
    assert check_status(today_harare() + timedelta(days=91)) == "Valid"


def test_expiry_far_in_the_future_is_valid():
    assert check_status(today_harare() + timedelta(days=365)) == "Valid"


def test_uses_the_harare_date_near_midnight_utc(frozen_clock):
    # At 23:30 UTC Harare is already on 2030-05-06: an expiry on that day
    # is "Due Soon", not "Valid".
    assert check_status(date(2030, 5, 6)) == "Due Soon"
