# tests/test_employee_import.py — Batch 4: the Mining Excel importer.
#
# The browser sends normalised rows + column mapping; the server re-validates
# everything. Preview dry-runs, commit writes + logs, undo deletes through the
# normal path. Unit tests cover dates/names/mapping; router tests mount the
# real importer on the harness fake.
import copy

import pytest

from app.employee_import import (
    PlanError,
    canonical_department,
    normalize_type,
    parse_import_date,
    split_full_name,
    plan_import,
)
from tests.scope_harness import FakeSupabase, mount_router

ADMIN = {"user_id": "admin-1", "email": "admin@example.com", "role": "admin", "scope": None}
MANAGER = {"user_id": "m-1", "email": "m@example.com", "role": "manager", "scope": None}

WIDE_MAPPING = {
    "0": "mine_number", "1": "full_name", "2": "job_title",
    "3": "section", "4": {"target": "ppe:helmet", "label": "Hard hat"},
    "5": {"target": "size:helmet"}, "6": {"target": "ppe:boots", "label": "Boots"},
    "7": "date_engaged", "8": "phone",
}


def _store():
    return {
        "employees": [
            {"id": 1, "employee_id": "C0952", "department": "Engineering",
             "first_name": "Takudzai", "last_name": "Chibi", "designation": "Electrician",
             "section": "Electrical", "date_of_engagement": "2020-01-01",
             "id_number": "ID1", "phone": "0711"},
            {"id": 2, "employee_id": "M0001", "department": "Mining",
             "first_name": "Farai", "last_name": "Moyo", "designation": "Fitter",
             "section": "Mining", "date_of_engagement": "2021-06-01",
             "id_number": "ID2", "phone": ""},
            {"id": 3, "employee_id": "M0002", "department": "Mining",
             "first_name": "Tinashe", "last_name": "Sibanda", "designation": "Boilermaker",
             "section": "Mining", "date_of_engagement": "2022-01-01",
             "id_number": "ID3", "phone": "0777"},
        ],
        "ppe_records": [
            {"id": 1, "employee_id": "C0952", "department": "Engineering",
             "ppe_type": "helmet", "item_name": "Hard hat", "issue_date": "2026-01-10",
             "status": "active"},
            {"id": 2, "employee_id": "M0001", "department": "Mining",
             "ppe_type": "boots", "item_name": "Boots", "issue_date": "2026-02-01",
             "status": "active"},
        ],
        "ppe_matrix": [
            {"ppe_type": "helmet", "interval_months": 30},
        ],
        "employee_imports": [],
    }


@pytest.fixture
def imp_client(monkeypatch):
    import app.routers.employee_import as mod
    return mount_router(monkeypatch, mod, "/api/employees/import", _store())


# ─── units: dates, names, departments ─────────────────────────────────────────

def test_parse_import_date_shapes():
    assert parse_import_date("") == (None, False)
    assert parse_import_date(None) == (None, False)
    assert parse_import_date("2025-10-03") == ("2025-10-03", False)
    assert parse_import_date("12/03/2024") == ("2024-03-12", False)
    assert parse_import_date("12-03-2024") == ("2024-03-12", False)
    assert parse_import_date("Oct 2025") == ("2025-10-01", True)
    assert parse_import_date("october 2025") == ("2025-10-01", True)
    assert parse_import_date("next week") == (None, False)
    assert parse_import_date("31/02/2024") == (None, False)
    assert parse_import_date(1) == (None, False)
    assert parse_import_date(True) == (None, False)


def test_parse_import_date_excel_serials():
    from datetime import date, timedelta
    serial = (date(2024, 3, 12) - date(1899, 12, 30)).days
    assert parse_import_date(serial) == ("2024-03-12", False)
    assert parse_import_date(float(serial)) == ("2024-03-12", False)


def test_split_full_name_shapes():
    assert split_full_name("MOYO Tendai") == ("Tendai", "MOYO")
    assert split_full_name("Tendai Moyo") == ("Tendai", "Moyo")
    assert split_full_name("MOYO") == ("", "MOYO")
    assert split_full_name("Tendai") == ("Tendai", "")
    assert split_full_name("") == ("", "")
    assert split_full_name("MOYO T K") == ("T K", "MOYO")
    assert split_full_name("Jean-Marie van Damme") == ("Jean-Marie van", "Damme")


def test_canonical_department():
    assert canonical_department("mining") == "Mining"
    with pytest.raises(PlanError):
        canonical_department("Mars")


def test_normalize_type():
    assert normalize_type(" Hard Hat ") == "hard hat"
    assert normalize_type(None) == ""


# ─── plan: wide layout ───────────────────────────────────────────────────────

def _wide_rows():
    return [
        ["MINING REGISTER OCTOBER 2025", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],
        ["M0001", "MOYO Farai", "Fitter", "Mining", "10/03/2024", "10", "01/02/2026", "2021-06-01", "0712345678"],
        ["M0002", "Tinashe Sibanda", "Fitter", "Mining", "", "", "", "", "0777"],
        ["M0003", "Tendai Ncube", "General Hand", "", "15/01/2024", "", "", "", ""],
        ["C0952", "Takudzai Chibi", "Electrician", "", "10/01/2024", "", "", "", ""],
        ["M0009", "", "", "", "", "", "", "", ""],
    ]


def _plan(rows=None, mapping=None, layout="wide"):
    db = FakeSupabase(_store(), [])
    return plan_import(db, rows if rows is not None else _wide_rows(),
                       mapping if mapping is not None else WIDE_MAPPING, layout)


def test_preview_wide_counts_and_actions():
    plan = _plan()
    assert plan["counts"]["rows_seen"] == 7
    assert plan["counts"]["rows_ignored"] == 2
    people = {p["mine_number"]: p for p in plan["people"]}
    assert people["M0001"]["action"] == "fill"
    assert people["M0001"]["fill"] == {"phone": "0712345678"}
    assert people["M0001"]["diffs"] == []
    assert people["M0001"]["person_id"] == 2
    assert people["M0002"]["action"] == "unchanged"
    assert people["M0002"]["fill"] == {}
    assert people["M0002"]["diffs"] == [{"field": "job_title", "was": "Boilermaker", "filed": "Fitter"}]
    assert people["M0003"]["action"] == "create"
    assert people["M0003"]["section"] == ""
    assert people["C0952"]["action"] == "refused"
    assert people["M0009"]["action"] == "refused"


def test_preview_engineering_collision_names_the_person():
    plan = _plan()
    reasons = [p["reason"] for p in plan["problems"]]
    assert "C0952 is already Takudzai Chibi in Engineering." in reasons


def test_preview_title_and_blank_rows_ignored():
    plan = _plan()
    assert all(p.get("number") != "MINING REGISTER OCTOBER 2025" for p in plan["problems"])
    assert plan["counts"]["people_refused"] == 2


def test_preview_ppe_add_skip_and_expiry():
    plan = _plan()
    adds = [e for e in plan["ppe"] if e["action"] == "add"]
    skipped = [e for e in plan["ppe"] if e["action"] == "duplicate_skipped"]
    # M0001 helmet 2024-03-10: new, with the matrix override (30 months).
    helmets = [e for e in adds if e["ppe_type"] == "helmet"]
    assert [(e["mine_number"], e["issue_iso"]) for e in helmets] == [
        ("M0001", "2024-03-10"), ("M0003", "2024-01-15")]
    assert {e["mine_number"]: e["expiry_date"] for e in helmets} == {
        "M0001": "2026-09-10", "M0003": "2026-07-15"}
    assert helmets[1]["size"] is None
    # M0001 helmet 2024-03-10 is also new (live has nothing that day).
    assert ("M0001", "2024-03-10") in [(e["mine_number"], e["issue_iso"]) for e in adds]
    assert [e["size"] for e in adds if e["mine_number"] == "M0001"] == ["10"]
    # M0001 boots 2026-02-01 duplicates the live issue: skipped.
    assert [(e["mine_number"], e["issue_iso"]) for e in skipped] == [("M0001", "2026-02-01")]
    # C0952's issue is refused with the person (M0009's row has no issues).
    assert {e["mine_number"] for e in plan["ppe"] if e["action"] == "refused"} == {"C0952"}


def test_preview_unreadable_date_refused_with_plain_reason():
    db = FakeSupabase(_store(), [])
    rows = [["M0004", "A B", "", "", "next week", "", "", "", ""]]
    plan = plan_import(db, rows, WIDE_MAPPING, "wide")
    refused = [e for e in plan["ppe"] if e["action"] == "refused"]
    assert len(refused) == 1
    assert "next week" in refused[0]["reason"] and "Hard hat" in refused[0]["reason"]


def test_preview_mapping_errors_are_422_plain():
    db = FakeSupabase(_store(), [])
    with pytest.raises(PlanError, match="mine number"):
        plan_import(db, [["x"]], {"0": "first_name"}, "wide")
    with pytest.raises(PlanError, match="Unknown PPE type"):
        plan_import(db, [["M0004"]], {"0": "mine_number", "1": "ppe:jetpack"}, "wide")
    with pytest.raises(PlanError, match="pairs with no PPE"):
        plan_import(db, [["M0004"]], {"0": "mine_number", "1": "size:helmet"}, "wide")
    with pytest.raises(PlanError, match="first \\+ surname"):
        plan_import(db, [["M0004"]], {"0": "mine_number", "1": "full_name", "2": "first_name"}, "wide")


# ─── plan: long layout ───────────────────────────────────────────────────────

LONG_MAPPING = {
    "0": "mine_number", "1": "full_name", "2": "item_text",
    "3": "size", "4": "issue_date",
}


def test_preview_long_layout_item_text_and_raw_unknowns():
    db = FakeSupabase(_store(), [])
    rows = [
        ["M0004", "SIBANDA Tinashe", "HELMET", "58", "03/03/2024"],
        ["M0004", "SIBANDA Tinashe", "Dust mask", "", "04/03/2024"],
        ["M0004", "SIBANDA Tinashe", "HELMET", "58", "03/03/2024"],
        ["M0005", "", "", "", ""],
    ]
    plan = plan_import(db, rows, LONG_MAPPING, "long")
    people = {p["mine_number"]: p for p in plan["people"]}
    assert people["M0004"]["action"] == "create"
    assert (people["M0004"]["first_name"], people["M0004"]["last_name"]) == ("Tinashe", "SIBANDA")
    adds = [e for e in plan["ppe"] if e["action"] == "add"]
    assert [(e["ppe_type"], e["size"]) for e in adds] == [("helmet", "58"), ("Dust mask", None)]
    unknown = [e for e in adds if e.get("unknown_item")]
    assert [e["item_name"] for e in unknown] == ["Dust mask"]
    assert plan["counts"]["unknown_items"] == 1
    assert plan["counts"]["ppe_duplicates_skipped"] == 1
    assert people["M0005"]["action"] == "refused"


def test_long_layout_needs_exactly_one_item_and_date_column():
    db = FakeSupabase(_store(), [])
    with pytest.raises(PlanError, match="item text"):
        plan_import(db, [["M0004"]], {"0": "mine_number", "4": "issue_date"}, "long")


def test_plan_is_case_insensitive_on_numbers():
    db = FakeSupabase(_store(), [])
    plan = plan_import(db, [["m0001", "Farai Moyo", "", "", "", "", "", "", ""]],
                       WIDE_MAPPING, "wide")
    assert plan["people"][0]["action"] in ("fill", "unchanged")


# ─── commit + undo through the router ────────────────────────────────────────

def _commit_body(rows=None, mapping=None, layout="wide"):
    return {"file_name": "mining.xlsx", "layout": layout, "department": "Mining",
            "rows": rows if rows is not None else _wide_rows(),
            "mapping": mapping if mapping is not None else WIDE_MAPPING}


def test_commit_creates_and_logs(imp_client):
    imp_client.as_user(ADMIN)
    resp = imp_client.post("/api/employees/import/commit", json=_commit_body())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["people_created"] == 1
    assert body["counts"]["people_filled"] == 1
    assert body["counts"]["ppe_created"] == 2
    assert body["counts"]["ppe_duplicates_skipped"] == 1
    assert len(body["created_employee_ids"]) == 1
    assert len(body["created_ppe_ids"]) == 2
    assert body["import_id"] is not None
    store = imp_client.scope_store
    assert len(store["employees"]) == 4
    assert len(store["ppe_records"]) == 4
    assert len(store["employee_imports"]) == 1
    assert store["employee_imports"][0]["department"] == "Mining"


def test_rerun_creates_nothing_new(imp_client):
    imp_client.as_user(ADMIN)
    first = imp_client.post("/api/employees/import/commit", json=_commit_body()).json()
    second = imp_client.post("/api/employees/import/commit", json=_commit_body()).json()
    assert second["counts"]["people_created"] == 0
    assert second["counts"]["ppe_created"] == 0
    assert second["counts"]["ppe_duplicates_skipped"] == 3
    assert len(imp_client.scope_store["employee_imports"]) == 2
    assert first["import_id"] != second["import_id"]


def test_undo_removes_what_the_commit_created(imp_client):
    imp_client.as_user(ADMIN)
    import_id = imp_client.post("/api/employees/import/commit", json=_commit_body()).json()["import_id"]
    store = imp_client.scope_store
    assert len(store["employees"]) == 4
    resp = imp_client.post(f"/api/employees/import/{import_id}/undo")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["removed_people"]) == 1
    assert len(body["removed_ppe"]) == 2
    assert body["kept_people"] == []
    assert len(store["employees"]) == 3
    assert len(store["ppe_records"]) == 2
    assert store["employee_imports"][0]["undone_at"] is not None
    again = imp_client.post(f"/api/employees/import/{import_id}/undo").json()
    assert again["already_undone"] is True


def test_undo_keeps_people_with_newer_records(imp_client):
    imp_client.as_user(ADMIN)
    import_id = imp_client.post("/api/employees/import/commit", json=_commit_body()).json()["import_id"]
    store = imp_client.scope_store
    created = [e for e in store["employees"] if e["employee_id"] == "M0003"][0]
    store.setdefault("leaves", []).append({"id": 1, "employee_id": "M0003"})
    body = imp_client.post(f"/api/employees/import/{import_id}/undo").json()
    assert body["removed_people"] == []
    assert [k["mine_number"] for k in body["kept_people"]] == ["M0003"]
    assert body["kept_people"][0]["tables"] == ["leaves"]
    assert len(body["removed_ppe"]) == 2
    assert any(e["employee_id"] == "M0003" for e in store["employees"])


def test_undo_missing_is_404(imp_client):
    imp_client.as_user(ADMIN)
    assert imp_client.post("/api/employees/import/nope/undo").status_code == 404


def test_importer_routes_require_admin():
    # The harness swaps every role gate for the acting user, so the gate is
    # proven here at the wiring level: each import route depends on
    # require_role("admin"). (A manager through the real app gets 403.)
    import app.routers.employee_import as mod
    for route in mod.router.routes:
        dependant = getattr(route, "dependant", None)
        assert dependant is not None, route.path
        needed = []

        def walk(node):
            for sub in node.dependencies:
                fn = sub.call
                if fn is not None and getattr(fn, "__qualname__", "") == \
                        "require_role.<locals>._check":
                    needed.append(dict(zip(fn.__code__.co_freevars,
                                           [c.cell_contents for c in (fn.__closure__ or [])])).get("min_role"))
                walk(sub)

        walk(dependant)
        assert needed and all(r == "admin" for r in needed), route.path


def test_preview_endpoint_matches_plan(imp_client):
    imp_client.as_user(ADMIN)
    resp = imp_client.post("/api/employees/import/preview", json=_commit_body())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["people_new"] == 1
    assert body["counts"]["ppe_new"] == 2
    assert len(imp_client.scope_store["employee_imports"]) == 0
