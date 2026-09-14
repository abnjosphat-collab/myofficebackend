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
