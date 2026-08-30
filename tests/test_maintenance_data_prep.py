# tests/test_maintenance_data_prep.py — prepare_data_for_db/prepare_data_for_response
# had zero tests despite real data-integrity rules: date/datetime objects -> ISO
# strings, dicts/lists passed through un-stringified (JSONB columns), and — the
# non-obvious one — an empty string in a date/time column becomes None so PostgreSQL
# doesn't reject it, but ONLY for columns actually in _DATE_FIELDS/_TIME_FIELDS (an
# empty string elsewhere is left as a plain empty string, not silently nulled).

from datetime import date, datetime

from app.routers.maintenance import prepare_data_for_db, prepare_data_for_response


def test_date_object_becomes_iso_string():
    result = prepare_data_for_db({"date_raised": date(2024, 3, 15)})
    assert result["date_raised"] == "2024-03-15"


def test_datetime_object_becomes_iso_string():
    result = prepare_data_for_db({"created_at": datetime(2024, 3, 15, 10, 30)})
    assert result["created_at"] == "2024-03-15T10:30:00"


def test_dict_value_is_passed_through_unstringified():
    result = prepare_data_for_db({"job_type": {"category": "Mechanical"}})
    assert result["job_type"] == {"category": "Mechanical"}


def test_list_value_is_passed_through_unstringified():
    result = prepare_data_for_db({"spares_used": [{"name": "Bearing", "qty": 2}]})
    assert result["spares_used"] == [{"name": "Bearing", "qty": 2}]


def test_empty_string_in_a_known_date_field_becomes_none():
    result = prepare_data_for_db({"due_date": ""})
    assert result["due_date"] is None


def test_empty_string_in_an_unrelated_field_stays_an_empty_string():
    # Only date/time columns get the empty->None treatment - an empty string
    # elsewhere (e.g. free-text notes) must not be silently nulled.
    result = prepare_data_for_db({"notes": ""})
    assert result["notes"] == ""


def test_normal_scalar_values_pass_through_unchanged():
    result = prepare_data_for_db({"priority": "high", "estimated_hours": 4})
    assert result == {"priority": "high", "estimated_hours": 4}


def test_response_parses_json_string_fields():
    result = prepare_data_for_response({"job_type": '{"category": "Electrical"}'})
    assert result["job_type"] == {"category": "Electrical"}


def test_response_leaves_malformed_json_as_the_raw_string():
    result = prepare_data_for_response({"manpower": "not valid json"})
    assert result["manpower"] == "not valid json"


def test_response_leaves_non_json_fields_untouched():
    result = prepare_data_for_response({"priority": "high"})
    assert result["priority"] == "high"


def test_response_leaves_already_parsed_json_fields_untouched():
    # If the value already came back as a real list/dict (not a string), don't
    # attempt to json.loads() it.
    result = prepare_data_for_response({"spares_used": [{"name": "Filter"}]})
    assert result["spares_used"] == [{"name": "Filter"}]
