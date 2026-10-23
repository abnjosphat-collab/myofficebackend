# tests/test_scope.py — the section-scope registry and its helpers: unscoped
# viewers pass through untouched, scoped viewers match their section exactly,
# unknown tables fail closed, and creates stamp (never trust the browser).
import pytest
from fastapi import HTTPException

import app.scope as scope_mod
from app.scope import (
    TABLES,
    apply_list_scope,
    assert_row_visible,
    filter_rows,
    row_visible,
    scope_person_ids,
    stamp_create,
    viewer_scope,
)

SCOPED = {"role": "user", "scope": "Projects"}
UNSCOPED = {"role": "user", "scope": None}
LEGACY = {"role": "manager"}
ADMIN = {"role": "admin", "scope": None}


class _Q:
    def __init__(self, rows, calls):
        self._rows = list(rows)
        self._calls = calls

    def eq(self, col, val):
        self._calls.append(("eq", col, val))
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def in_(self, col, vals):
        self._calls.append(("in", col, list(vals)))
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self

    def or_(self, s):
        self._calls.append(("or", s))
        return self

    def select(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _DB:
    def __init__(self, store):
        self.store = store
        self.calls = []

    def table(self, name):
        return _Q(self.store.get(name, []), self.calls)


@pytest.fixture()
def db():
    return _DB({
        "employees": [
            {"id": 1, "employee_id": "C1001", "section": "Projects"},
            {"id": 2, "employee_id": "C1002", "section": "Projects"},
            {"id": 3, "employee_id": "C2001", "section": "Mechanical"},
        ],
        "sheq_inspections": [
            {"id": 41, "section": "Projects"},
            {"id": 42, "section": "Mechanical"},
        ],
        "equipment": [
            {"id": 31, "section": "Projects"},
            {"id": 32, "section": "Mechanical"},
        ],
    })


def test_viewer_scope_reads_top_level_first_and_tolerates_old_sessions():
    assert viewer_scope(SCOPED) == "Projects"
    assert viewer_scope(UNSCOPED) is None
    assert viewer_scope(LEGACY) is None
    assert viewer_scope(None) is None
    assert viewer_scope({"role": "user", "permissions": {"scope": ""}}) is None
    # Raw profile rows (permissions nested, no top-level key) still resolve.
    assert viewer_scope({"role": "user", "permissions": {"scope": "Projects"}}) == "Projects"
    assert viewer_scope({"role": "user", "scope": "", "permissions": {"scope": "Projects"}}) == "Projects"


def test_registry_covers_the_batch_0_inventory():
    from tests.test_projects_scope import SCOPED_TABLES

    assert SCOPED_TABLES <= set(TABLES)


def test_scope_person_ids_collects_both_id_kinds(db):
    ids = scope_person_ids(db, "Projects")
    assert ids == {"register": {"C1001", "C1002"}, "db": {1, 2}, "names": set()}


def test_scope_person_ids_collects_normalized_names():
    store = _DB({"employees": [
        {"id": 1, "employee_id": "C1001", "section": "Projects",
         "first_name": "  Pam", "last_name": " Moyo "},
        {"id": 2, "employee_id": "C1002", "section": "Projects",
         "first_name": "", "last_name": ""},
        {"id": 3, "employee_id": "C2001", "section": "Mechanical",
         "first_name": "Mike", "last_name": "Doe"},
    ]})
    assert scope_person_ids(store, "Projects")["names"] == {"pam moyo"}


def test_empty_scope_predicates_match_nothing_instead_of_erroring(db):
    empty = {"register": set(), "db": set(), "names": set()}
    q = db.table("leaves")
    apply_list_scope(q, SCOPED, "leaves", db, empty)
    assert ("eq", "employee_id", "__scope_empty__") in db.calls
    q = db.table("artisan_timesheets")
    apply_list_scope(q, SCOPED, "artisan_timesheets", db, empty)
    assert ("eq", "employee_db_id", -1) in db.calls


def test_unscoped_list_queries_pass_through_untouched(db):
    q = db.table("leaves")
    assert apply_list_scope(q, UNSCOPED, "leaves", db) is q
    assert db.calls == []
    assert apply_list_scope(q, None, "leaves", db) is q


def test_section_list_adds_an_exact_match(db):
    q = db.table("nearmiss_reports")
    apply_list_scope(q, SCOPED, "nearmiss_reports", db)
    assert ("eq", "section", "Projects") in db.calls


def test_person_list_filters_to_in_scope_ids(db):
    q = db.table("leaves")
    apply_list_scope(q, SCOPED, "leaves", db)
    assert ("in", "employee_id", ["C1001", "C1002"]) in db.calls


def test_dual_link_list_builds_one_or_of_two_in_lists(db):
    q = db.table("artisan_timesheets")
    apply_list_scope(q, SCOPED, "artisan_timesheets", db)
    assert ("or", 'employee_db_id.in.(1,2),employee_id.in.("C1001","C1002")') in db.calls


def test_dual_link_row_visible_matches_either_link(db):
    ids = scope_person_ids(db, "Projects")
    assert row_visible(SCOPED, "artisan_timesheets", {"employee_db_id": 1, "employee_id": "CX"}, db, ids)
    assert row_visible(SCOPED, "artisan_timesheets", {"employee_db_id": 99, "employee_id": "C1001"}, db, ids)
    assert row_visible(SCOPED, "artisan_timesheets", {"employee_db_id": None, "employee_id": "C1002"}, db, ids)
    assert not row_visible(SCOPED, "artisan_timesheets", {"employee_db_id": 99, "employee_id": "CX"}, db, ids)
    assert not row_visible(SCOPED, "artisan_timesheets", {"employee_db_id": None, "employee_id": None}, db, ids)


def test_json_nested_person_link_filters_in_python(db):
    ids = scope_person_ids(db, "Projects")
    assert row_visible(SCOPED, "ppe_order_list", {"entry": {"employee_id": "C1001"}}, db, ids)
    assert not row_visible(SCOPED, "ppe_order_list", {"entry": {"employee_id": "CX"}}, db, ids)
    assert not row_visible(SCOPED, "ppe_order_list", {"entry": {}}, db, ids)
    assert not row_visible(SCOPED, "ppe_order_list", {}, db, ids)
    assert not row_visible(SCOPED, "ppe_order_list", {"entry": None}, db, ids)
    with pytest.raises(RuntimeError):
        apply_list_scope(db.table("ppe_order_list"), SCOPED, "ppe_order_list", db, ids)
    out = stamp_create(SCOPED, "ppe_order_list", {"entry": {"employee_id": "C1002"}}, db, ids)
    assert out["entry"]["employee_id"] == "C1002"
    with pytest.raises(HTTPException):
        stamp_create(SCOPED, "ppe_order_list", {"entry": {"employee_id": ""}}, db, ids)


def test_dual_link_stamp_needs_either_link(db):
    out = stamp_create(SCOPED, "artisan_timesheets", {"employee_id": "C1001", "employee_db_id": None}, db)
    assert out["employee_id"] == "C1001"
    with pytest.raises(HTTPException):
        stamp_create(SCOPED, "artisan_timesheets", {"employee_id": "CX", "employee_db_id": 99}, db)


def test_unknown_table_fails_closed_for_scoped_viewers(db):
    q = db.table("spares")
    with pytest.raises(RuntimeError):
        apply_list_scope(q, SCOPED, "spares", db)
    # ...but unscoped viewers never touch the registry at all.
    assert apply_list_scope(q, UNSCOPED, "spares", db) is q


def test_row_visible_section_exact_and_null_never_matches():
    assert row_visible(SCOPED, "equipment", {"section": "Projects"})
    assert not row_visible(SCOPED, "equipment", {"section": "Mechanical"})
    assert not row_visible(SCOPED, "equipment", {"section": None})
    assert not row_visible(SCOPED, "equipment", {})
    assert row_visible(UNSCOPED, "equipment", {"section": "Mechanical"})


def test_row_visible_keeps_general_complaints_visible_to_all():
    assert row_visible(SCOPED, "safety_complaints", {"section": "Projects"})
    assert row_visible(SCOPED, "safety_complaints", {"section": "General"})
    assert row_visible(SCOPED, "safety_complaints", {"section": None})
    assert not row_visible(SCOPED, "safety_complaints", {"section": "Mechanical"})


def test_row_visible_person_matches_register_and_db_ids(db):
    ids = scope_person_ids(db, "Projects")
    assert row_visible(SCOPED, "leaves", {"employee_id": "C1001"}, db, ids)
    assert not row_visible(SCOPED, "leaves", {"employee_id": "C2001"}, db, ids)
    assert row_visible(SCOPED, "timesheets", {"employee_id": 2}, db, ids)
    assert not row_visible(SCOPED, "timesheets", {"employee_id": 3}, db, ids)


def test_row_visible_members_needs_one_in_scope_person(db):
    ids = scope_person_ids(db, "Projects")
    rotation = {"name": "R", "members": [{"employee_id": "C2001", "crew": [{"employee_id": "C1002"}]}]}
    assert row_visible(SCOPED, "duty_rotations", rotation, db, ids)
    assert not row_visible(SCOPED, "duty_rotations",
                            {"name": "R", "members": [{"employee_id": "C2001"}]}, db, ids)
    assert row_visible(SCOPED, "standby_rotations", {"section": "Projects", "members": []}, db, ids)
    assert row_visible(SCOPED, "rotation_covers",
                        {"absent_employee_id": "C2001", "cover_employee_id": "C1001"}, db, ids)
    assert not row_visible(SCOPED, "rotation_covers",
                            {"absent_employee_id": "C2001", "cover_employee_id": "C2009"}, db, ids)


def test_row_visible_inherits_parent_and_hides_unlinked(db):
    assert row_visible(SCOPED, "sheq_findings", {"inspection_id": 41}, db)
    assert not row_visible(SCOPED, "sheq_findings", {"inspection_id": 42}, db)
    assert not row_visible(SCOPED, "sheq_findings", {"inspection_id": None}, db)
    assert not row_visible(SCOPED, "sheq_findings", {"inspection_id": 404}, db)


def test_filter_rows_returns_input_untouched_for_unscoped():
    rows = [{"section": "Mechanical"}]
    assert filter_rows(UNSCOPED, "duty_rotations", rows) is rows


def test_assert_row_visible_404s_missing_and_out_of_scope_alike():
    with pytest.raises(HTTPException) as exc:
        assert_row_visible(SCOPED, "equipment", None)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        assert_row_visible(SCOPED, "equipment", {"section": "Mechanical"})
    assert exc.value.status_code == 404
    assert assert_row_visible(SCOPED, "equipment", {"section": "Projects"}) == {"section": "Projects"}


def test_stamp_create_forces_section_and_refuses_out_of_scope_people(db):
    out = stamp_create(SCOPED, "equipment", {"name": "M", "section": "Mechanical"}, db)
    assert out["section"] == "Projects"
    out = stamp_create(SCOPED, "leaves", {"employee_id": "C1001"}, db)
    assert out["employee_id"] == "C1001"
    with pytest.raises(HTTPException) as exc:
        stamp_create(SCOPED, "leaves", {"employee_id": "C2001"}, db)
    assert exc.value.status_code == 404
    # Unscoped creates pass through byte-identical.
    payload = {"name": "M", "section": "Mechanical"}
    assert stamp_create(UNSCOPED, "equipment", dict(payload), db) == payload


def test_stamp_create_members_needs_a_visible_row(db):
    visible = {"section": "Projects", "members": [{"employee_id": "C2001"}]}
    assert stamp_create(SCOPED, "standby_rotations", dict(visible), db) == visible
    visible = {"members": [{"employee_id": "C1001"}]}
    assert stamp_create(SCOPED, "duty_rotations", dict(visible), db) == visible
    with pytest.raises(HTTPException) as exc:
        stamp_create(SCOPED, "duty_rotations", {"members": [{"employee_id": "C2001"}]}, db)
    assert exc.value.status_code == 404


def test_stamp_create_validates_the_parent(db):
    out = stamp_create(SCOPED, "sheq_findings", {"inspection_id": 41}, db)
    assert out["inspection_id"] == 41
    with pytest.raises(HTTPException):
        stamp_create(SCOPED, "sheq_findings", {"inspection_id": 42}, db)


def test_every_registry_entry_names_a_known_rule():
    known = {"section", "person", "members", "inherits", "subtree"}
    for table, spec in TABLES.items():
        assert spec["rule"] in known, table
        if spec["rule"] in ("section", "person"):
            assert "column" in spec, table
        if spec["rule"] == "inherits":
            assert spec["parent"] in TABLES, table
