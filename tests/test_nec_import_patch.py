from app.nec_import.patch_builder import build_patch


def test_unresolved_normal_skipped():
    patch, reason = build_patch({"date": "2026-08-13", "interpreted": {"status": "work"}}, None, "C0001", [])
    assert patch is None
    assert reason == "skip_unresolved_normal"


def test_off_zero_normal():
    patch, reason = build_patch(
        {"date": "2026-08-13", "interpreted": {"status": "off", "normal_hours_expected": 0}},
        None,
        "C0001",
        [],
    )
    assert reason == "upsert"
    assert patch["status"] == "off"
    assert patch["regular_hours"] == 0
    assert patch["overtime_hours"] == 0


def test_leave_day_without_module_record_skipped():
    patch, reason = build_patch(
        {"date": "2026-08-13", "interpreted": {"status": "leave", "normal_hours_expected": 8}},
        None,
        "C0001",
        [],
    )
    assert patch is None
    assert reason == "skip_leave_expected_no_module_record"


def test_does_not_clear_existing_overtime_on_update():
    existing = {"id": 1, "overtime_hours": 4, "regular_hours": 8, "status": "work"}
    patch, reason = build_patch(
        {"date": "2026-08-13", "interpreted": {"status": "work", "normal_hours_expected": 10}},
        existing,
        "C0001",
        [],
    )
    assert reason == "upsert"
    assert "overtime_hours" not in patch


def test_scan_times_written_but_never_cleared():
    patch, _ = build_patch(
        {"date": "2026-08-13", "interpreted": {
            "status": "work", "normal_hours_expected": 10,
            "time_in": "07:00", "time_out": "19:00",
        }},
        None,
        "C0001",
        [],
    )
    assert patch["start_time"] == "07:00"
    assert patch["end_time"] == "19:00"
    patch2, _ = build_patch(
        {"date": "2026-08-13", "interpreted": {"status": "work", "normal_hours_expected": 10}},
        {"id": 1, "regular_hours": 8, "status": "work", "start_time": "07:00", "end_time": "17:00"},
        "C0001",
        [],
    )
    assert "start_time" not in patch2
    assert "end_time" not in patch2


def test_proven_zero_night_clears_stale_value_and_moves_total():
    existing = {
        "id": 1, "regular_hours": 10, "status": "work",
        "nightshift_hours": 12, "nightshift_allowance": True, "total_hours": 25,
    }
    patch, reason = build_patch(
        {"date": "2026-08-13", "interpreted": {
            "status": "work", "normal_hours_expected": 10,
            "time_in": "07:00", "time_out": "17:00", "night_proven_zero": True,
        }},
        existing,
        "C0001",
        [],
    )
    assert reason == "upsert"
    assert patch["nightshift_hours"] == 0
    assert patch["nightshift_allowance"] is False
    assert patch["total_hours"] == 13


def test_visible_no_standby_clears_flag():
    existing = {"id": 1, "regular_hours": 10, "status": "work", "standby_allowance": True}
    patch, reason = build_patch(
        {"date": "2026-08-13", "interpreted": {
            "status": "work", "normal_hours_expected": 10, "standby_clear": True,
        }},
        existing,
        "C0001",
        [],
    )
    assert reason == "upsert"
    assert patch["standby_allowance"] is False
