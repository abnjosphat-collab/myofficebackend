# tests/test_employees_date_conversion.py — _dates_to_db/_dates_from_db round-trip
# date fields (date_of_engagement, ppe_issue_date) between Python date objects and
# Supabase's ISO strings, plus normalize 4 array fields to [] instead of None/missing.
# Zero prior tests despite being real data-integrity round-trip logic.

from datetime import date

from app.routers.employees import _dates_to_db, _dates_from_db


def test_to_db_converts_date_objects_to_iso_strings():
    result = _dates_to_db({"date_of_engagement": date(2020, 3, 15)})
    assert result["date_of_engagement"] == "2020-03-15"


def test_to_db_leaves_non_date_values_untouched():
    result = _dates_to_db({"date_of_engagement": None, "name": "John"})
    assert result["date_of_engagement"] is None
    assert result["name"] == "John"


def test_from_db_parses_iso_strings_to_date_objects():
    result = _dates_from_db({"date_of_engagement": "2020-03-15"})
    assert result["date_of_engagement"] == date(2020, 3, 15)


def test_from_db_malformed_date_string_becomes_none_not_a_crash():
    result = _dates_from_db({"date_of_engagement": "not-a-date"})
    assert result["date_of_engagement"] is None


def test_from_db_normalizes_missing_array_fields_to_empty_list():
    result = _dates_from_db({})
    assert result["qualifications"] == []
    assert result["offences"] == []
    assert result["awards_recognition"] == []
    assert result["other_positions"] == []


def test_from_db_preserves_existing_array_values():
    result = _dates_from_db({"qualifications": ["Trade Cert"]})
    assert result["qualifications"] == ["Trade Cert"]


def test_from_db_does_not_mutate_the_input_dict():
    original = {"date_of_engagement": "2020-03-15"}
    _dates_from_db(original)
    assert original["date_of_engagement"] == "2020-03-15"  # still the raw string
