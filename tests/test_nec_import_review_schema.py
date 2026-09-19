from app.nec_import.review_schema import validate_review_payload


def test_rejects_automatic_writes():
    ok, errs = validate_review_payload({"automatic_writes_allowed": True, "period": {}, "sheets": []})
    assert not ok


def test_minimal_valid():
    ok, errs = validate_review_payload({
        "automatic_writes_allowed": False,
        "period": {"start_date": "2026-08-13", "end_date": "2026-09-12"},
        "sheets": [{"sheet_id": "s1", "rows": []}],
    })
    assert ok
    assert errs == []


def test_rejects_invalid_row_date_and_negative_hours():
    ok, errs = validate_review_payload({
        "automatic_writes_allowed": False,
        "period": {"start_date": "2026-08-13", "end_date": "2026-09-12"},
        "sheets": [{
            "sheet_id": "s1",
            "rows": [{"date": "not-a-date", "interpreted": {"normal_hours_expected": -1}}],
        }],
    })
    assert not ok
    assert any("date invalid" in e for e in errs)
    assert any("normal_hours_expected" in e for e in errs)


def test_rejects_more_than_500_sheets():
    sheets = [{"sheet_id": f"s{i}", "rows": []} for i in range(501)]
    ok, errs = validate_review_payload({
        "automatic_writes_allowed": False,
        "period": {"start_date": "2026-08-13", "end_date": "2026-09-12"},
        "sheets": sheets,
    })
    assert not ok
    assert any("500" in e for e in errs)
