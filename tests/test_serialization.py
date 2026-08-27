# tests/test_serialization.py — encode_json_fields/decode_json_fields
# (app/serialization.py), extracted from four near-identical hand-rolled copies in
# timesheets.py, overtime.py, breakdowns.py, and daily_reports.py. Pure functions,
# no mocking needed.

from app.serialization import encode_json_fields, decode_json_fields


# ─── encode_json_fields ──────────────────────────────────────────────────────────

def test_encode_stringifies_a_list_field():
    out = encode_json_fields({"name": "x", "spares_used": [{"id": 1}]}, ["spares_used"])
    assert out["spares_used"] == '[{"id": 1}]'
    assert out["name"] == "x"  # untouched fields pass through


def test_encode_defaults_missing_field_to_empty_list():
    out = encode_json_fields({"name": "x"}, ["spares_used"])
    assert out["spares_used"] == "[]"


def test_encode_defaults_falsy_field_to_empty_list():
    # None or [] on the input both collapse to the same "[]" on the way in — matches
    # every original call site's own `or []` behavior.
    assert encode_json_fields({"spares_used": None}, ["spares_used"])["spares_used"] == "[]"
    assert encode_json_fields({"spares_used": []}, ["spares_used"])["spares_used"] == "[]"


def test_encode_does_not_mutate_the_input_dict():
    original = {"spares_used": [1, 2]}
    encode_json_fields(original, ["spares_used"])
    assert original["spares_used"] == [1, 2]


def test_encode_handles_multiple_fields():
    out = encode_json_fields({"call_outs": [1], "equipment": [2]}, ["call_outs", "equipment"])
    assert out["call_outs"] == "[1]"
    assert out["equipment"] == "[2]"


# ─── decode_json_fields ──────────────────────────────────────────────────────────

def test_decode_parses_a_json_string_field():
    out = decode_json_fields({"spares_used": '[{"id": 1}]'}, ["spares_used"])
    assert out["spares_used"] == [{"id": 1}]


def test_decode_falls_back_to_empty_list_on_malformed_json():
    out = decode_json_fields({"spares_used": "not json"}, ["spares_used"])
    assert out["spares_used"] == []


def test_decode_leaves_a_non_string_value_alone():
    # A JSONB column that was never stringified in the first place (Supabase/PostgREST
    # already returns it as a real list) must pass through unchanged, not get coerced.
    out = decode_json_fields({"spares_used": [{"id": 1}]}, ["spares_used"])
    assert out["spares_used"] == [{"id": 1}]


def test_decode_leaves_a_missing_field_alone():
    out = decode_json_fields({"name": "x"}, ["spares_used"])
    assert "spares_used" not in out


def test_decode_does_not_mutate_the_input_dict():
    original = {"spares_used": '[1, 2]'}
    decode_json_fields(original, ["spares_used"])
    assert original["spares_used"] == '[1, 2]'


def test_encode_then_decode_round_trips():
    data = {"spares_used": [{"id": 1, "qty": 2}]}
    round_tripped = decode_json_fields(encode_json_fields(data, ["spares_used"]), ["spares_used"])
    assert round_tripped["spares_used"] == data["spares_used"]
