"""Preview/apply orchestration (F01/F02) — not isolated patch_builder only."""
from pathlib import Path

import pytest

from app.nec_import import apply_runner, preview as preview_mod
from app.nec_import.apply_runner import run_import_from_review
from app.nec_import.preview import build_preview
from app.nec_import.sheet_selection import eligible_review_sheets


def _employee(db_id: int, human: str, *, nec: bool = True):
    return {
        "id": db_id,
        "employee_id": human,
        "first_name": "Test",
        "last_name": "User",
        "employment_type": "NEC" if nec else "Permanent",
        "is_active": True,
    }


def _review_with_sheet(*, disposition: str = "accepted"):
    return {
        "schema_version": 1,
        "period": {"start_date": "2026-08-13", "end_date": "2026-09-12"},
        "sheets": [
            {
                "sheet_id": "s1",
                "disposition": disposition,
                "employee": {"human_code": "C0001", "name_raw": "Test User"},
                "rows": [
                    {
                        "date": "2026-08-13",
                        "interpreted": {"status": "off", "normal_hours_expected": 0},
                    }
                ],
            }
        ],
    }


def test_eligible_review_sheets_excludes_rejected_and_superseded():
    sheets = [
        {"sheet_id": "a", "disposition": "accepted"},
        {"sheet_id": "b", "disposition": "rejected"},
        {"sheet_id": "c", "disposition": "superseded"},
    ]
    eligible = eligible_review_sheets(sheets)
    assert [s["sheet_id"] for s in eligible] == ["a"]


def test_build_preview_orchestrates_without_tuple_unpack_crash(monkeypatch):
    monkeypatch.setattr(preview_mod, "load_employees", lambda: [_employee(1, "C0001")])
    monkeypatch.setattr(preview_mod, "fetch_timesheets_map", lambda _s, _e: {})
    monkeypatch.setattr(preview_mod, "fetch_leaves", lambda: [])

    result = build_preview(_review_with_sheet(), "2026-08-13", "2026-09-12")

    assert result["stats"]["create"] == 1
    assert result["sheet_summaries"][0]["status"] == "matched"
    assert result["line_items"][0]["kind"] == "create"


def test_build_preview_skips_rejected_sheet(monkeypatch):
    monkeypatch.setattr(preview_mod, "load_employees", lambda: [_employee(1, "C0001")])
    monkeypatch.setattr(preview_mod, "fetch_timesheets_map", lambda _s, _e: {})
    monkeypatch.setattr(preview_mod, "fetch_leaves", lambda: [])

    result = build_preview(
        _review_with_sheet(disposition="rejected"), "2026-08-13", "2026-09-12"
    )

    assert result["sheet_summaries"] == []
    assert result["line_items"] == []
    assert result["stats"]["create"] == 0


def test_run_import_from_review_dry_run(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(apply_runner, "load_employees", lambda: [_employee(1, "C0001")])
    monkeypatch.setattr(apply_runner, "fetch_timesheets_map", lambda _s, _e: {})
    monkeypatch.setattr(apply_runner, "fetch_leaves", lambda: [])

    report = run_import_from_review(
        _review_with_sheet(),
        period_start="2026-08-13",
        period_end="2026-09-12",
        apply=False,
        snapshot_dir=tmp_path,
    )

    assert report["apply"] is False
    assert report["stats"]["planned_create"] == 1
    assert report["stats"]["created"] == 0
    assert (tmp_path).exists()


def test_run_import_aborts_apply_when_duplicate_sheets(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(apply_runner, "load_employees", lambda: [_employee(1, "C0001")])
    monkeypatch.setattr(apply_runner, "fetch_timesheets_map", lambda _s, _e: {})
    monkeypatch.setattr(apply_runner, "fetch_leaves", lambda: [])

    review = {
        "period": {"start_date": "2026-08-13", "end_date": "2026-09-12"},
        "sheets": [
            {
                "sheet_id": "s1",
                "disposition": "accepted",
                "employee": {"mine_no_raw": "C0001", "name_raw": "Test User"},
                "rows": [],
            },
            {
                "sheet_id": "s2",
                "disposition": "accepted",
                "employee": {"mine_no_raw": "C0001", "name_raw": "Test User"},
                "rows": [],
            },
        ],
    }

    report = run_import_from_review(
        review,
        period_start="2026-08-13",
        period_end="2026-09-12",
        apply=True,
        snapshot_dir=tmp_path,
    )

    assert report.get("aborted") is True
    assert report["abort_reason"] == "duplicate_sheet_groups"
    assert report["stats"]["created"] == 0
