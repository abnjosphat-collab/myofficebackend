from app.zim_holidays import MUNHUMUTAPA_DAY_FIRST_YEAR, zim_holiday_name, zim_holidays_for_year


def test_munhumutapa_day_from_2026_only():
    assert MUNHUMUTAPA_DAY_FIRST_YEAR == 2026
    assert zim_holiday_name("2025-09-15") is None
    assert zim_holiday_name("2026-09-15") == "Munhumutapa Day"
    h2025 = zim_holidays_for_year(2025)
    h2026 = zim_holidays_for_year(2026)
    assert "2025-09-15" not in h2025
    assert h2026["2026-09-15"] == "Munhumutapa Day"
    assert zim_holidays_for_year(2027)["2027-09-15"] == "Munhumutapa Day"
