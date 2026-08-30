# tests/test_spares_column_inference.py — the spreadsheet column-inference engine
# behind POST /spares/infer (clean_data, filter_for_db, _is_category_row,
# _extract_category_name, _score_column, _infer_column_roles, _safe_val). This is the
# logic that guesses which uploaded-spreadsheet column is stock_code/description/
# unit_price when someone bulk-imports spares — genuinely error-prone heuristic scoring
# with zero prior tests, in spares.py, the single lowest-coverage inventory/money
# module (26%). Assertions on the scoring functions check relative ranking (which role
# wins), not exact floats — the actual behavioral contract, not incidental weights that
# could shift without changing correctness.

from datetime import datetime

import polars as pl

from app.routers.spares import (
    clean_data, filter_for_db, _is_category_row, _extract_category_name,
    _score_column, _infer_column_roles, _safe_val,
)


# ─── clean_data ─────────────────────────────────────────────────────────────────────

def test_clean_data_preserves_none_values():
    # clean_data's only caller (update_spare) runs exclude_unset=True first, so a key
    # reaching here IS something the caller explicitly sent — an explicit None means
    # "clear this field", not "field wasn't provided". Used to drop it silently (the
    # null-vs-unset bug, already fixed project-wide once, backend edec24a) — fixed
    # here 2026-08-30. See tests/test_spares_crud.py for the update_spare-level test.
    assert clean_data({"a": None, "b": 1}) == {"a": None, "b": 1}


def test_clean_data_drops_empty_string():
    assert clean_data({"a": "", "b": "x"}) == {"b": "x"}


def test_clean_data_strips_whitespace_and_drops_if_blank():
    assert clean_data({"a": "  hi  ", "b": "   "}) == {"a": "hi"}


def test_clean_data_keeps_falsy_non_string_values():
    # 0 and False are real data, not "missing" — must survive (only None/"" are dropped).
    assert clean_data({"qty": 0, "safety_stock": False}) == {"qty": 0, "safety_stock": False}


# ─── filter_for_db ──────────────────────────────────────────────────────────────────

def test_filter_for_db_drops_unknown_columns():
    result = filter_for_db({"stock_code": "AB1", "not_a_real_column": "x"})
    assert result == {"stock_code": "AB1"}


# ─── _is_category_row ───────────────────────────────────────────────────────────────

def test_category_prefix_is_always_a_header():
    assert _is_category_row(("Category: Bearings", "", ""), is_bold=False) is True


def test_category_prefix_case_insensitive_short_form():
    assert _is_category_row(("Cat:Bearings",), is_bold=False) is True


def test_bold_row_with_no_numeric_values_is_a_header():
    assert _is_category_row(("Hydraulics", "", ""), is_bold=True) is True


def test_bold_row_with_a_numeric_value_is_not_a_header():
    # A bold data row (e.g. a highlighted in-stock item) still has a price/qty — must
    # not be misread as a category label.
    assert _is_category_row(("Bearings", "12.50"), is_bold=True) is False


def test_single_non_numeric_cell_without_bold_is_a_header():
    assert _is_category_row(("Standalone Label",), is_bold=False) is True


def test_single_numeric_cell_without_bold_is_not_a_header():
    assert _is_category_row(("125.00",), is_bold=False) is False


def test_normal_data_row_is_not_a_header():
    assert _is_category_row(("SKU123", "Bearing widget", "12.50"), is_bold=False) is False


def test_all_empty_values_is_not_a_header():
    assert _is_category_row((None, "", None), is_bold=False) is False


# ─── _extract_category_name ─────────────────────────────────────────────────────────

def test_extract_category_name_strips_prefix():
    assert _extract_category_name(("Category: Bearings",)) == "Bearings"


def test_extract_category_name_strips_prefix_no_space():
    assert _extract_category_name(("Cat:Bearings",)) == "Bearings"


def test_extract_category_name_no_prefix_uses_first_cell():
    assert _extract_category_name(("Hydraulics", "", "")) == "Hydraulics"


def test_extract_category_name_all_empty_is_blank():
    assert _extract_category_name((None, "", None)) == ""


# ─── _score_column — relative ranking, not exact floats ────────────────────────────

def test_stock_code_column_scores_highest_for_stock_code():
    series = pl.Series("Stock Code", ["AB-1234", "CD-5678", "EF-9012", "GH-3456"])
    scores = _score_column("Stock Code", series)
    assert scores["stock_code"] > scores["description"]
    assert scores["stock_code"] > scores["unit_price"]


def test_description_column_scores_highest_for_description():
    series = pl.Series("Description", [
        "Hydraulic hose fitting 3/4 inch brass", "Ball bearing 6205 2RS sealed",
        "Replacement filter cartridge for pump", "Steel bracket mounting plate",
    ])
    scores = _score_column("Description", series)
    assert scores["description"] > scores["stock_code"]
    assert scores["description"] > scores["unit_price"]


def test_price_column_scores_highest_for_unit_price():
    series = pl.Series("Unit Price", [12.50, 8.99, 145.00, 3.25])
    scores = _score_column("Unit Price", series)
    assert scores["unit_price"] > scores["stock_code"]
    assert scores["unit_price"] > scores["description"]


def test_qty_hint_vetoes_a_price_hint_in_the_same_header():
    # A header like "Amount in Stock" matches a price hint ("amount") AND a quantity
    # hint ("in stock") — the quantity hint must veto the price match, since this is a
    # stock-count column wearing price-ish wording, not an actual price column.
    series = pl.Series("x", [10, 25, 5, 100])
    ambiguous = _score_column("Amount in Stock", series)
    clear_price_header = _score_column("Amount", series)
    assert ambiguous["unit_price"] < clear_price_header["unit_price"]


def test_currency_formatted_string_column_is_scored_as_numeric():
    # ">80% of values parse as numbers once currency symbols/commas are stripped" is
    # what promotes a Utf8 column into the numeric-scoring branch instead of the
    # string-heuristics branch.
    series = pl.Series("Price", ["$1,250.00", "$899.50", "$45.00", "$12.99"], dtype=pl.Utf8)
    scores = _score_column("Price", series)
    assert scores["unit_price"] > scores["stock_code"]
    assert scores["unit_price"] > scores["description"]


def test_all_blank_string_column_returns_zero_scores():
    # Every value is whitespace-only — after stripping, zero non-empty strings remain,
    # hitting the early-return before any string-heuristic scoring runs.
    series = pl.Series("Blank", ["  ", " ", "\t"], dtype=pl.Utf8)
    scores = _score_column("Blank", series)
    assert scores == {"stock_code": 0.0, "description": 0.0, "unit_price": 0.0}


def test_empty_column_returns_zero_scores():
    # A neutral header — "Notes" would score via the short generic stock-code hint
    # "no" matching as a substring ("Notes" contains "no"), which isn't what this test
    # is checking (that's a separate, pre-existing header-matching quirk, not a bug
    # worth asserting on here).
    series = pl.Series("Extra", [None, None, None], dtype=pl.Utf8)
    scores = _score_column("Extra", series)
    assert scores == {"stock_code": 0.0, "description": 0.0, "unit_price": 0.0}


# ─── _infer_column_roles — full end-to-end inference on a synthetic spreadsheet ────

def test_infer_column_roles_picks_the_right_columns():
    df = pl.DataFrame({
        "Stock Code": ["AB-1234", "CD-5678", "EF-9012", "GH-3456", "IJ-7890"],
        "Description": [
            "Hydraulic hose fitting 3/4 inch brass connector",
            "Ball bearing 6205 2RS sealed unit",
            "Replacement filter cartridge for main pump",
            "Steel bracket mounting plate galvanized",
            "Rubber gasket seal ring set of 4",
        ],
        "Qty On Hand": [10, 25, 5, 100, 8],
        "Unit Price": [12.50, 8.99, 145.00, 3.25, 22.10],
    })
    result = _infer_column_roles(df)
    assert result["stock_code"] == "Stock Code"
    assert result["description"] == "Description"
    assert result["unit_price"] == "Unit Price"
    # Quantity column must never win the unit_price role despite being numeric.
    assert result["unit_price"] != "Qty On Hand"


def test_infer_column_roles_does_not_reuse_a_column_for_two_roles():
    df = pl.DataFrame({
        "Stock Code": ["AB-1234", "CD-5678", "EF-9012"],
        "Description": ["Hydraulic hose fitting brass", "Ball bearing sealed unit", "Filter cartridge pump"],
        "Unit Price": [12.50, 8.99, 145.00],
    })
    result = _infer_column_roles(df)
    assigned = [result[role] for role in ("stock_code", "description", "unit_price") if role in result]
    assert len(assigned) == len(set(assigned))


# ─── _safe_val ───────────────────────────────────────────────────────────────────────

def test_safe_val_passes_through_json_native_types():
    assert _safe_val(None) is None
    assert _safe_val(5) == 5
    assert _safe_val(5.5) == 5.5
    assert _safe_val("x") == "x"
    assert _safe_val(True) is True


def test_safe_val_stringifies_everything_else():
    d = datetime(2024, 1, 1)
    assert _safe_val(d) == str(d)
