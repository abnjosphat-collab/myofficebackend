# tests/test_server_error_mapping.py — server_error() is the one place a caught exception becomes a response, so it must
# (1) hand a deliberate 4xx back unchanged, (2) turn a database constraint refusal into the right 409/422 without
# leaking internals, and (3) keep every other failure a generic 500 with a reference.
import pytest
from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.errors import server_error


def refusal(code, message="", details=""):
    return APIError({"message": message, "code": code, "hint": None, "details": details})


def test_a_deliberate_404_survives_a_broad_except():
    original = HTTPException(status_code=404, detail="Leave with ID 9 not found")
    assert server_error(original, "Updating leave") is original


def test_a_deliberate_409_survives_a_broad_except():
    original = HTTPException(status_code=409, detail="Already has overtime")
    assert server_error(original, "Creating overtime").status_code == 409


def test_unique_violation_is_409_and_hides_the_constraint():
    exc = refusal("23505", 'duplicate key value violates unique constraint "some_internal_idx"', "Key (employee_id, date)=(PM486, 2026-03-04) already exists.")
    out = server_error(exc, "Creating overtime")
    assert out.status_code == 409
    assert "some_internal_idx" not in out.detail and "PM486" not in out.detail


def test_a_known_unique_index_gets_its_own_sentence():
    exc = refusal("23505", 'duplicate key value violates unique constraint "overtime_one_live_request_per_slot"')
    out = server_error(exc, "Creating overtime")
    assert out.status_code == 409 and "overtime request" in out.detail and "Edit that request" in out.detail


def test_foreign_key_to_a_missing_record_is_422():
    exc = refusal("23503", 'insert or update on table "overtime" violates foreign key constraint "overtime_employee_fk"', 'Key (employee_id)=(ZZ9) is not present in table "employees".')
    out = server_error(exc, "Creating overtime")
    assert out.status_code == 422 and "does not exist" in out.detail and "ZZ9" not in out.detail


def test_foreign_key_from_a_record_still_in_use_is_409():
    exc = refusal("23503", 'update or delete on table "employees" violates foreign key constraint "x"', 'Key (employee_id)=(C1) is still referenced from table "overtime".')
    out = server_error(exc, "Deleting employee")
    assert out.status_code == 409 and "cannot be removed" in out.detail


def test_check_and_not_null_violations_are_422():
    assert server_error(refusal("23514", 'violates check constraint "c"'), "Saving").status_code == 422
    assert server_error(refusal("23502", 'null value in column "x"'), "Saving").status_code == 422


def test_anything_else_is_a_generic_500_with_a_reference():
    out = server_error(RuntimeError("connection refused to db.internal:5432"), "Loading overtime")
    assert out.status_code == 500
    assert "connection refused" not in out.detail
    assert out.detail.startswith("Loading overtime failed. Please try again. (ref ")


def test_an_unrelated_error_code_is_still_a_500():
    assert server_error(refusal("42P01", 'relation "x" does not exist'), "Loading").status_code == 500


def test_exceptions_without_a_code_attribute_are_500():
    class Odd(Exception):
        code = 12345  # not a string
    assert server_error(Odd("x"), "Loading").status_code == 500
