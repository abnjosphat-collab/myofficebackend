"""Employee matching, patch building and extraction provider selection."""
import pytest

from app.nec_import import employee_match as em
from app.nec_import import patch_builder as pb
from app.nec_import.extraction import registry
from app.nec_import.review_schema import validate_review_payload


# ── employee_match ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    (None, None), ("", None), ("   ", None),
    ("c 12", "C0012"), ("C0012", "C0012"),
    ("pp5", "PP005"), (" pp058 ", "PP058"),
    ("abc1", "ABC1"),
])
def test_normalize_code(raw, expected):
    assert em.normalize_code(raw) == expected


def test_normalize_name_collapses_whitespace_and_case():
    assert em.normalize_name("  John   SMITH ") == "john smith"
    assert em.normalize_name(None) == ""


def test_sheet_human_code_uses_special_case_candidate_code_and_raw():
    assert em.sheet_human_code({"employee": {"name_raw": "T. Chidakwa", "mine_no_raw": "C1"}}) == "PP058"
    assert em.sheet_human_code({"employee": {"mine_no_raw": "c 7"}}) == "C0007"
    assert em.sheet_human_code({"employee": {"candidate_employee_code": "pp9"}}) == "PP009"
    assert em.sheet_human_code({}) is None


def _roster():
    return [
        {"employee_id": "C0001", "first_name": "Ann", "last_name": "Moyo"},
        {"employee_id": "PP002", "first_name": "Peter", "last_name": "Ncube"},
        {"employee_id": "", "first_name": "Zodwa", "last_name": "Dube"},
    ]


def test_index_employees_skips_blank_codes_but_indexes_names():
    by_code, by_name = em.index_employees(_roster())
    assert set(by_code) == {"C0001", "PP002"}
    assert "zodwa dube" in by_name


def test_resolve_employee_by_code_then_name_then_prefix_then_unresolved():
    by_code, by_name = em.index_employees(_roster())

    emp, evidence, cands = em.resolve_employee({"employee": {"mine_no_raw": "c1"}}, by_code, by_name)
    assert emp["last_name"] == "Moyo" and evidence == "code:C0001" and cands == []

    emp, evidence, _ = em.resolve_employee({"employee": {"name_raw": "Peter  NCUBE"}}, by_code, by_name)
    assert emp["employee_id"] == "PP002" and evidence.startswith("name:")

    emp, evidence, _ = em.resolve_employee({"employee": {"name_raw": "Peter Ncubeson"}}, by_code, by_name)
    assert emp["employee_id"] == "PP002" and evidence.startswith("name_prefix:")

    emp, evidence, cands = em.resolve_employee({"employee": {"name_raw": "Nobody At All"}}, by_code, by_name)
    assert emp is None and evidence.startswith("unresolved") and cands == []


def test_resolve_employee_reports_ambiguous_candidates_without_choosing():
    rows = [
        {"employee_id": "C0001", "first_name": "Tendai", "last_name": "Moyo"},
        {"employee_id": "C0002", "first_name": "Tendai", "last_name": "Mpofu"},
    ]
    by_code, by_name = em.index_employees(rows)
    emp, evidence, cands = em.resolve_employee({"employee": {"name_raw": "Tendai Mx"}}, by_code, by_name)
    assert emp is None and evidence.startswith("unresolved")
    assert {c["employee_id"] for c in cands} == {"C0001", "C0002"}


# ── patch_builder ─────────────────────────────────────────────────────────────
LEAVE = {"employee_id": "c 1", "start_date": "2026-08-14", "end_date": "2026-08-16", "status": "approved"}


def _row(date="2026-08-20", **interp):
    return {"date": date, "interpreted": interp}


def test_leave_on_date_matches_normalised_code_and_range_and_ignores_rejected():
    assert pb.leave_on_date([LEAVE], "C0001", "2026-08-15") is LEAVE
    assert pb.leave_on_date([LEAVE], "C0001", "2026-08-17") is None
    assert pb.leave_on_date([LEAVE], "C0002", "2026-08-15") is None
    assert pb.leave_on_date([{**LEAVE, "status": "rejected"}], "C0001", "2026-08-15") is None


@pytest.mark.parametrize("status,expected", [("off", "off"), ("work", "work"), ("leave", None), ("sick", None), ("??", None), (None, None)])
def test_map_work_status(status, expected):
    assert pb.map_work_status(status) == expected


def test_build_patch_skips_rows_without_expected_hours():
    assert pb.build_patch(_row(status="work"), None, "C0001", []) == (None, "skip_unresolved_normal")


def test_build_patch_new_work_day_sets_defaults_and_totals():
    patch, reason = pb.build_patch(
        _row(status="work", normal_hours_expected=8, night_allowance_hours=2, standby_marked=True),
        None, "C0001", [],
    )
    assert reason == "upsert"
    assert patch == {
        "status": "work", "regular_hours": 8.0, "overtime_hours": 0, "holiday_overtime_hours": 0,
        "nightshift_hours": 2.0, "nightshift_allowance": True, "standby_allowance": True, "total_hours": 10.0,
    }


def test_build_patch_off_day_has_zero_regular_hours():
    patch, _ = pb.build_patch(_row(status="off", normal_hours_expected=8), None, "C0001", [])
    assert patch["regular_hours"] == 0.0 and patch["total_hours"] == 0.0


def test_build_patch_unmapped_status_is_skipped():
    assert pb.build_patch(_row(status="mystery", normal_hours_expected=8), None, "C0001", []) == (None, "skip_unmapped_status")


def test_build_patch_existing_identical_record_is_unchanged_and_never_zeroes_overtime():
    existing = {"status": "work", "regular_hours": 8, "total_hours": 8, "overtime_hours": 3}
    patch, reason = pb.build_patch(_row(status="work", normal_hours_expected=8), existing, "C0001", [])
    assert (patch, reason) == (None, "skip_unchanged")
    patch, reason = pb.build_patch(_row(status="work", normal_hours_expected=7), existing, "C0001", [])
    assert reason == "upsert" and "overtime_hours" not in patch and patch["regular_hours"] == 7.0


def test_build_patch_keeps_hours_a_stored_total_already_counts():
    """A stored total may include module overtime; the import moves it only by the regular/night change it makes."""
    existing = {"status": "work", "regular_hours": 10, "nightshift_hours": 0, "overtime_hours": 2,
                "holiday_overtime_hours": 10, "total_hours": 22, "standby_allowance": True}
    same = pb.build_patch(_row(status="work", normal_hours_expected=10, standby_marked=True), existing, "C0001", [])
    assert same == (None, "skip_unchanged")
    patch, reason = pb.build_patch(_row(status="work", normal_hours_expected=8), existing, "C0001", [])
    assert reason == "upsert" and patch["total_hours"] == 20.0


def test_build_patch_total_includes_existing_night_hours():
    existing = {"status": "work", "regular_hours": 8, "nightshift_hours": 1.5, "nightshift_allowance": True}
    patch, _ = pb.build_patch(_row(status="work", normal_hours_expected=6), existing, "C0001", [])
    assert patch["total_hours"] == 7.5


def test_build_patch_leave_day_without_module_record_is_flagged():
    assert pb.build_patch(_row(status="leave", normal_hours_expected=8), None, "C0001", []) == (
        None, "skip_leave_expected_no_module_record")


def test_build_patch_leave_day_only_carries_extras():
    row_plain = _row(date="2026-08-15", status="work", normal_hours_expected=8)
    assert pb.build_patch(row_plain, None, "C0001", [LEAVE]) == (None, "skip_module_leave_day")
    assert pb.build_patch(row_plain, {"status": "leave"}, "C0001", [LEAVE]) == (None, "skip_module_leave_no_extras")

    row_extras = _row(date="2026-08-15", status="work", normal_hours_expected=8,
                      night_allowance_hours=3, standby_marked=True)
    patch, reason = pb.build_patch(row_extras, None, "C0001", [LEAVE])
    assert reason == "patch_leave_day_extras"
    assert patch == {"nightshift_hours": 3.0, "nightshift_allowance": True, "standby_allowance": True}


# ── extraction registry ───────────────────────────────────────────────────────
@pytest.mark.parametrize("env,cls", [
    (None, registry.ManualPendingProvider),
    ("manual", registry.ManualPendingProvider),
    (" Review_JSON ", registry.ManualPendingProvider),
    ("uploaded", registry.ReviewJsonUploadProvider),
    ("review_json_upload", registry.ReviewJsonUploadProvider),
    ("some-unknown-vendor", registry.ManualPendingProvider),
])
def test_get_extraction_provider_selection(monkeypatch, env, cls):
    if env is None:
        monkeypatch.delenv("NEC_IMPORT_EXTRACTION_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("NEC_IMPORT_EXTRACTION_PROVIDER", env)
    assert isinstance(registry.get_extraction_provider(), cls)


def test_default_provider_refuses_to_extract_with_clear_message(monkeypatch):
    monkeypatch.delenv("NEC_IMPORT_EXTRACTION_PROVIDER", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        registry.run_extraction("job", "2026-08-13", "2026-09-12")


def test_run_extraction_validates_uploaded_review(monkeypatch):
    monkeypatch.setenv("NEC_IMPORT_EXTRACTION_PROVIDER", "uploaded")
    good = {"period": {"start_date": "2026-08-13", "end_date": "2026-09-12"}, "sheets": []}
    monkeypatch.setattr("app.nec_import.job_store.load_review_json", lambda job_id: good)
    assert registry.run_extraction("j", "a", "b") == good

    monkeypatch.setattr("app.nec_import.job_store.load_review_json", lambda job_id: {"sheets": "x"})
    with pytest.raises(ValueError, match="period"):
        registry.run_extraction("j", "a", "b")


# ── review_schema edges ───────────────────────────────────────────────────────
@pytest.mark.parametrize("payload,fragment", [
    ([], "root must be an object"),
    ({"period": {"start_date": "2026-13-45", "end_date": "2026-09-12"}, "sheets": []}, "YYYY-MM-DD"),
    ({"period": {"start_date": "2026-08-13", "end_date": "2026-09-12"}, "sheets": "x"}, "sheets must be an array"),
    ({"period": {"start_date": "2026-08-13", "end_date": "2026-09-12"}, "sheets": ["x"]}, "must be object"),
    ({"period": {"start_date": "2026-08-13", "end_date": "2026-09-12"}, "sheets": [{"rows": "x"}]}, "rows must be array"),
    ({"period": {"start_date": "2026-08-13", "end_date": "2026-09-12"}, "sheets": [{"rows": ["x"]}]}, "rows[0] must be object"),
])
def test_review_payload_rejections(payload, fragment):
    ok, errs = validate_review_payload(payload)
    assert not ok
    assert any(fragment in e for e in errs), errs
