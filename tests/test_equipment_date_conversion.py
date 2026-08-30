# tests/test_equipment_date_conversion.py — process_dates_for_db/process_dates_from_db
# round-trip 3 date fields (purchase_date, warranty_expiry, commission_date) between
# Python date objects and Supabase's ISO strings, plus normalize tags to [] when None.
# Zero prior tests despite being real data-integrity round-trip logic.

from datetime import date

from app.routers.equipment import process_dates_for_db, process_dates_from_db


def test_for_db_converts_date_objects_to_iso_strings():
    result = process_dates_for_db({"purchase_date": date(2022, 6, 1)})
    assert result["purchase_date"] == "2022-06-01"


def test_for_db_leaves_string_dates_untouched():
    # Already a string (not a date object) — isinstance check must not double-convert.
    result = process_dates_for_db({"purchase_date": "2022-06-01"})
    assert result["purchase_date"] == "2022-06-01"


def test_from_db_parses_iso_strings_to_date_objects():
    result = process_dates_from_db({"warranty_expiry": "2025-12-31"})
    assert result["warranty_expiry"] == date(2025, 12, 31)


def test_from_db_malformed_date_string_becomes_none_not_a_crash():
    result = process_dates_from_db({"commission_date": "garbage"})
    assert result["commission_date"] is None


def test_from_db_normalizes_none_tags_to_empty_list():
    result = process_dates_from_db({"tags": None})
    assert result["tags"] == []


def test_from_db_preserves_existing_tags():
    result = process_dates_from_db({"tags": ["critical", "site-a"]})
    assert result["tags"] == ["critical", "site-a"]


def test_for_db_does_not_mutate_the_input_dict():
    original = {"purchase_date": date(2022, 6, 1)}
    process_dates_for_db(original)
    assert original["purchase_date"] == date(2022, 6, 1)  # still the original date object
