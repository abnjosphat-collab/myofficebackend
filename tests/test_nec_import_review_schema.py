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
