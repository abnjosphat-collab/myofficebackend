# tests/test_compliance_lubrication_status.py — compliance.py's _compute_status
# (overdue/due_soon/current on a 30-day window) and lubrication.py's _lube_status
# (same shape, but a 7-day window) had zero tests. Worth testing both explicitly
# since they're easy to confuse with each other (same shape, different thresholds) or
# with the several other expiry-status functions covered elsewhere this pass
# (compressors' 7/30-day service urgency, training's 90-day cert expiry).

from datetime import date, timedelta

from app.routers.compliance import _compute_status
from app.routers.lubrication import _lube_status


def _iso(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


# ─── compliance._compute_status — 30-day window ─────────────────────────────────────

def test_compliance_no_expiry_is_current():
    assert _compute_status(None) == "current"


def test_compliance_overdue():
    assert _compute_status(_iso(-1)) == "overdue"


def test_compliance_exactly_30_days_out_is_due_soon():
    assert _compute_status(_iso(30)) == "due_soon"


def test_compliance_31_days_out_is_current():
    assert _compute_status(_iso(31)) == "current"


def test_compliance_malformed_date_is_current_not_a_crash():
    assert _compute_status("not-a-date") == "current"


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


def test_lube_malformed_date_is_current_not_a_crash():
    assert _lube_status("garbage") == "current"
