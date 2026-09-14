from datetime import date

from app.nec_import.period import iter_period_dates, nec_period_for_payroll_month


def test_nec_period_september_2026():
    start, end = nec_period_for_payroll_month(2026, 9)
    assert start == date(2026, 8, 13)
    assert end == date(2026, 9, 12)


def test_nec_period_january_crosses_year():
    start, end = nec_period_for_payroll_month(2027, 1)
    assert start == date(2026, 12, 13)
    assert end == date(2027, 1, 12)


def test_iter_period_inclusive_31_days():
    dates = iter_period_dates(date(2026, 8, 13), date(2026, 9, 12))
    assert len(dates) == 31
    assert dates[0] == "2026-08-13"
    assert dates[-1] == "2026-09-12"
