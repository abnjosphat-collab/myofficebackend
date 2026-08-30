# tests/test_drivers_write_normalization.py — _clean_driver_write/_before_create/
# _before_update (drivers.py's CrudRouter before_create/before_update hooks) had zero
# tests despite real write-time normalization: trimming, dropping blank phone numbers,
# and collapsing an empty string back to None specifically because CrudRouter's create
# path already drops actual None via exclude_none but not "" — this is the one place
# that distinction actually matters.

from app.routers.drivers import _clean_driver_write, _before_create, _before_update


def test_trims_full_name():
    result = _clean_driver_write({"full_name": "  John Doe  "})
    assert result["full_name"] == "John Doe"


def test_drops_blank_phone_numbers_and_trims_the_rest():
    result = _clean_driver_write({"phone_numbers": ["  0771234567  ", "", "   ", "0779999999"]})
    assert result["phone_numbers"] == ["0771234567", "0779999999"]


def test_non_list_phone_numbers_is_left_untouched():
    # Defensive isinstance guard — a malformed non-list value shouldn't crash the list comp.
    result = _clean_driver_write({"phone_numbers": "not-a-list"})
    assert result["phone_numbers"] == "not-a-list"


def test_collapses_blank_optional_fields_to_none():
    result = _clean_driver_write({"department": "", "license_class": "", "notes": ""})
    assert result["department"] is None
    assert result["license_class"] is None
    assert result["notes"] is None


def test_preserves_non_blank_optional_fields():
    result = _clean_driver_write({"department": "Mechanical", "notes": "On probation"})
    assert result["department"] == "Mechanical"
    assert result["notes"] == "On probation"


def test_before_create_stamps_both_created_and_updated_at():
    result = _before_create({"full_name": "John"})
    assert result["created_at"] == result["updated_at"]
    assert result["created_at"]  # non-empty


def test_before_update_stamps_only_updated_at():
    result = _before_update({"full_name": "John"})
    assert "updated_at" in result
    assert "created_at" not in result
