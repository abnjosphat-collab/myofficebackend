# tests/test_safety_complaints_mapping.py — map_row's default values (category
# "General", priority "medium", status "open") are real business defaults for a
# newly-migrated or partially-filled row, not just cosmetic fallbacks, and had zero
# tests.

from app.routers.safety_complaints import map_row


def test_defaults_when_fields_are_missing():
    result = map_row({"id": "1"})
    assert result["category"] == "General"
    assert result["priority"] == "medium"
    assert result["section"] == "General"
    assert result["status"] == "open"


def test_preserves_explicit_values():
    result = map_row({"id": "1", "category": "Housekeeping", "priority": "high", "status": "closed"})
    assert result["category"] == "Housekeeping"
    assert result["priority"] == "high"
    assert result["status"] == "closed"


def test_maps_snake_case_to_camel_case():
    result = map_row({"raised_by": "J. Moyo", "issue_raised": "Loose railing", "by_who": "T. Ncube"})
    assert result["raisedBy"] == "J. Moyo"
    assert result["issueRaised"] == "Loose railing"
    assert result["byWho"] == "T. Ncube"
