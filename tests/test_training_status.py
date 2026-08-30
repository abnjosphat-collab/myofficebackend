# tests/test_training_status.py — check_status() classifies a certification's
# expiry into Expired/Due Soon/Valid, the compliance-status shown for every training
# record in the app. Zero prior tests despite being real compliance-relevant logic with
# a clear boundary (exactly 90 days out) worth locking in precisely. Dates are computed
# relative to date.today() at test-run time, not hardcoded, since this logic must keep
# working correctly on any future date, not just today's.

from datetime import date, timedelta

from app.routers.training import check_status, find_record, CERTIFICATIONS_DB


def test_past_expiry_is_expired():
    assert check_status(date.today() - timedelta(days=1)) == "Expired"


def test_expiry_today_is_not_expired():
    # `< today`, not `<= today` — a certificate expiring today hasn't lapsed yet.
    assert check_status(date.today()) == "Due Soon"


def test_expiry_exactly_90_days_out_is_due_soon():
    assert check_status(date.today() + timedelta(days=90)) == "Due Soon"


def test_expiry_91_days_out_is_valid():
    assert check_status(date.today() + timedelta(days=91)) == "Valid"


def test_expiry_far_in_the_future_is_valid():
    assert check_status(date.today() + timedelta(days=365)) == "Valid"


def test_find_record_locates_by_id():
    target = CERTIFICATIONS_DB[0]
    assert find_record(target.id) is target


def test_find_record_missing_id_returns_none():
    assert find_record("nonexistent-id") is None
