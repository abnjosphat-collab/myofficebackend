# tests/test_document_folders.py — folders: made anywhere, renamed and moved with their files, deleted only when empty, and the
# AMS structure created without ever duplicating a folder that is already there.
import pytest

from app import document_structure as ds
from tests._documents_client import U, make_client
from tests._documents_fake import FakeDb, doc, folder


@pytest.fixture
def db():
    return FakeDb({"documents": [], "document_folders": [], "document_activities": []})


@pytest.fixture
def client(monkeypatch, db):
    return make_client(monkeypatch, db)


def make(client, name, parent_id=None, **extra):
    return client.post("/api/documents/folders", json={"name": name, "parent_id": parent_id, **extra})


def folders(db):
    return db.tables["document_folders"]


def by_name(db, name):
    return [f for f in folders(db) if f["name"] == name]


# ─── create ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_folder_can_be_made_at_the_top_level_and_inside_another(client, db):
    top = make(client, "Contracts", description=" 2026 ")
    assert top.status_code == 200 and top.json()["parent_id"] is None and top.json()["description"] == "2026"
    assert top.json()["created_by"] == "Edson Mavhondo"
    inner = make(client, "Signed", top.json()["id"])
    assert inner.status_code == 200 and inner.json()["parent_id"] == top.json()["id"]


def test_made_folders_come_after_the_seeded_ones(client):
    assert make(client, "Mine").json()["sort_order"] == ds.CUSTOM_SORT_ORDER > max(n.sort_order for n in ds.flatten())


def test_names_are_tidied_and_checked(client):
    assert make(client, "  Site    photos ").json()["name"] == "Site photos"
    for bad in ("", "   ", "..", "back\\slash", "x" * 121):
        assert make(client, bad).status_code == 422


def test_a_name_is_unique_within_its_folder_ignoring_case_but_not_across_folders(client):
    a = make(client, "Reports").json()["id"]
    b = make(client, "Archive").json()["id"]
    dup = make(client, "REPORTS")
    assert dup.status_code == 409 and "already exists here" in dup.json()["detail"]
    assert make(client, "Reports", b).status_code == 200  # same name, different place
    assert make(client, "Reports", a).status_code == 200


def test_a_folder_in_an_unknown_parent_is_refused(client):
    assert make(client, "x", U(99)).status_code == 404
    assert make(client, "x", "bad-id").status_code == 422


def test_folders_cannot_go_deeper_than_eight_levels(client):
    parent = None
    for level in range(8):
        r = make(client, f"L{level}", parent)
        assert r.status_code == 200
        parent = r.json()["id"]
    too_deep = make(client, "L8", parent)
    assert too_deep.status_code == 422 and "levels deep" in too_deep.json()["detail"]


# ─── rename and move ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def tree(db):
    db.tables["document_folders"] += [folder(U(1), "A"), folder(U(2), "B", U(1)), folder(U(3), "C", U(2)), folder(U(4), "D")]
    db.tables["documents"] += [
        doc(U(11), "in-c.pdf", folder_ref=U(3), folder_path="A > B > C"),
        doc(U(12), "in-b.pdf", folder_ref=U(2), folder_path="A > B"),
        doc(U(13), "in-d.pdf", folder_ref=U(4), folder_path="D"),
    ]


def put(client, id, **body):
    return client.put(f"/api/documents/folders/{id}", json=body)


def path_of(db, doc_id):
    return next(d for d in db.tables["documents"] if d["id"] == doc_id)["folder_path"]


def test_renaming_a_folder_updates_the_path_of_every_file_below_it(client, db, tree):
    assert put(client, U(2), name="Bee").status_code == 200
    assert path_of(db, U(11)) == "A > Bee > C" and path_of(db, U(12)) == "A > Bee" and path_of(db, U(13)) == "D"


def test_moving_a_folder_carries_its_subfolders_and_files(client, db, tree):
    assert put(client, U(2), parent_id=U(4)).status_code == 200
    assert next(f for f in folders(db) if f["id"] == U(2))["parent_id"] == U(4)
    assert path_of(db, U(11)) == "D > B > C" and path_of(db, U(12)) == "D > B"


def test_a_folder_can_move_to_the_top_level(client, db, tree):
    assert put(client, U(2), parent_id=None).status_code == 200
    assert next(f for f in folders(db) if f["id"] == U(2))["parent_id"] is None and path_of(db, U(11)) == "B > C"


def test_leaving_the_parent_out_keeps_the_folder_where_it_is(client, db, tree):
    put(client, U(2), name="Bee")
    assert next(f for f in folders(db) if f["id"] == U(2))["parent_id"] == U(1)


def test_a_folder_cannot_go_into_itself_or_below_itself(client, tree):
    for target in (U(2), U(3)):
        r = put(client, U(2), parent_id=target)
        assert r.status_code == 422 and "into itself" in r.json()["detail"]


def test_a_rename_or_move_cannot_take_a_name_that_is_already_there(client, tree):
    assert put(client, U(4), name="a").status_code == 409
    client.post("/api/documents/folders", json={"name": "B", "parent_id": None})
    assert put(client, U(2), parent_id=None).status_code == 409  # a top-level "B" now exists


def test_renaming_to_itself_with_other_capitals_is_fine(client, tree):
    assert put(client, U(4), name="d").status_code == 200


def test_changing_nothing_changes_nothing(client, db, tree):
    before = [dict(f) for f in folders(db)]
    assert put(client, U(4), name="D").status_code == 200
    assert folders(db) == before


def test_an_unknown_folder_is_a_404(client, tree):
    assert put(client, U(99), name="x").status_code == 404
    assert put(client, "bad", name="x").status_code == 404


def test_folder_changes_are_in_the_history(client, db, tree):
    put(client, U(4), name="Delta")
    put(client, U(2), parent_id=U(4))
    actions = [a["action"] for a in db.tables["document_activities"]]
    assert actions == ["folder_update", "folder_move"]
    assert db.tables["document_activities"][0]["details"]["previous"] == "D"


# ─── delete ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_an_empty_folder_can_be_deleted(client, db, tree):
    empty = make(client, "Empty").json()["id"]
    assert client.delete(f"/api/documents/folders/{empty}").status_code == 200
    assert all(f["id"] != empty for f in folders(db))


def test_a_folder_with_subfolders_is_not_deleted(client, db, tree):
    r = client.delete(f"/api/documents/folders/{U(1)}")
    assert r.status_code == 409 and "still has 1 subfolder" in r.json()["detail"] and len(folders(db)) == 4


def test_a_folder_with_files_is_not_deleted_and_the_files_are_kept(client, db, tree):
    r = client.delete(f"/api/documents/folders/{U(4)}")
    assert r.status_code == 409 and "still holds 1 file" in r.json()["detail"] and "never deletes files" in r.json()["detail"]
    assert len(db.tables["documents"]) == 3


def test_a_folder_whose_files_are_in_the_trash_is_not_deleted_either(client, db, tree):
    db.tables["documents"][2]["deleted_at"] = "2026-10-19T09:00:00+00:00"
    r = client.delete(f"/api/documents/folders/{U(4)}")
    assert r.status_code == 409 and "in the trash" in r.json()["detail"]


def test_deleting_a_folder_is_for_managers(monkeypatch, db, tree):
    staff = make_client(monkeypatch, db, role="user")
    empty = staff.post("/api/documents/folders", json={"name": "Empty"}).json()["id"]  # anyone signed in can make one
    assert staff.delete(f"/api/documents/folders/{empty}").status_code == 403
    assert staff.put(f"/api/documents/folders/{empty}", json={"name": "Renamed"}).status_code == 200


def test_deleting_an_unknown_folder_is_a_404(client):
    assert client.delete(f"/api/documents/folders/{U(99)}").status_code == 404


# ─── setting up the AMS structure ──────────────────────────────────────────────────────────────────────────────────

SEED = "/api/documents/folders/seed-ams"
TOTAL = len(ds.flatten())


def test_it_creates_the_whole_structure_on_an_empty_library(client, db):
    out = client.post(SEED).json()
    assert out == {"created": TOTAL, "adopted": 0, "skipped": 0, "total": TOTAL} and len(folders(db)) == TOTAL


def test_every_created_folder_sits_under_its_parent_in_the_right_order(client, db):
    client.post(SEED)
    names = {f["id"]: f for f in folders(db)}
    root = by_name(db, ds.ROOT_NAME)[0]
    assert root["parent_id"] is None and root["seed_key"] == "ams"
    for f in folders(db):
        if f is not root:
            assert f["parent_id"] in names
    planning = next(f for f in folders(db) if f["name"] == "3. Planning")
    kids = sorted((f for f in folders(db) if f["parent_id"] == planning["id"]), key=lambda f: f["sort_order"])
    assert [k["name"] for k in kids] == ["3.1 Risk Management", "3.2 AM Objectives & KPIs", "3.3 Asset Management Plans (AMP)", "3.4 Budget, Forecast & Demand Planning", "3.5 Change Management (Engineering Change)"]
    assert planning["sort_order"] < ds.CUSTOM_SORT_ORDER


def test_pressing_it_again_creates_nothing(client, db):
    client.post(SEED)
    again = client.post(SEED).json()
    assert again == {"created": 0, "adopted": 0, "skipped": TOTAL, "total": TOTAL} and len(folders(db)) == TOTAL


def test_the_ten_folders_the_upgrade_sql_made_are_skipped_not_repeated(client, db):
    top = [n for n in ds.flatten() if n.depth <= 1]
    ids = {}
    for i, n in enumerate(top, start=1):
        ids[n.key] = U(i)
        db.tables["document_folders"].append(folder(U(i), n.name, ids.get(n.parent_key), seed_key=n.key))
    out = client.post(SEED).json()
    assert out["skipped"] == 10 and out["created"] == TOTAL - 10 and len(folders(db)) == TOTAL


def test_a_seeded_folder_that_was_renamed_is_not_made_again(client, db):
    client.post(SEED)
    risk = by_name(db, "3.1 Risk Management")[0]
    client.put(f"/api/documents/folders/{risk['id']}", json={"name": "Risk"})
    client.post(SEED)
    assert len(folders(db)) == TOTAL and by_name(db, "Risk") and not by_name(db, "3.1 Risk Management")


def test_folders_that_were_made_by_hand_are_adopted_with_their_files(client, db):
    root = folder(U(1), ds.ROOT_NAME, None, seed_key="ams")
    support = folder(U(2), "4. Support", U(1), seed_key="ams/4-support")
    hand = folder(U(3), "documented information", U(2))           # made by hand, no number
    custom = folder(U(4), "SOPs", U(2))                           # not part of the structure: stays
    db.tables["document_folders"] += [root, support, hand, custom]
    db.tables["documents"] += [doc(U(11), "sop.docx", folder_ref=U(3), folder_path="AMS > 4. Support > documented information"), doc(U(12), "x.pdf", folder_ref=U(4))]
    out = client.post(SEED).json()
    assert out["adopted"] == 1 and out["created"] == TOTAL - 3
    adopted = next(f for f in folders(db) if f["id"] == U(3))
    assert adopted["name"] == "4.4 Documented Information" and adopted["seed_key"] == "ams/4-support/4-4-documented-information"
    moved = next(d for d in db.tables["documents"] if d["id"] == U(11))
    assert moved["folder_ref"] == U(3) and moved["folder_path"].endswith("4. Support > 4.4 Documented Information")  # the file never moved
    assert next(f for f in folders(db) if f["id"] == U(4))["name"] == "SOPs"
    assert len(by_name(db, "Documented Information")) == 0 and len([f for f in folders(db) if f["name"].startswith("4.4")]) == 1


def test_subfolders_of_an_adopted_folder_are_found_and_kept(client, db):
    db.tables["document_folders"] += [
        folder(U(1), ds.ROOT_NAME, None, seed_key="ams"), folder(U(2), "3. Planning", U(1), seed_key="ams/3-planning"),
        folder(U(3), "Risk Management", U(2)), folder(U(4), "Asset Risk Register", U(3)),
    ]
    client.post(SEED)
    assert len(by_name(db, "Asset Risk Register")) == 1 and by_name(db, "Asset Risk Register")[0]["id"] == U(4)
    assert by_name(db, "Asset Risk Register")[0]["seed_key"].endswith("asset-risk-register")


def test_only_a_manager_can_set_it_up(monkeypatch, db):
    staff = make_client(monkeypatch, db, role="user")
    assert staff.post(SEED).status_code == 403 and folders(db) == []


def test_a_failed_setup_is_reported_not_silent(client, db):
    db.fail_ops = {"insert"}
    r = client.post(SEED)
    assert r.status_code == 500 and "ref " in r.json()["detail"]


def test_two_managers_pressing_it_at_once_get_a_plain_refusal(client, db, monkeypatch):
    import app.routers.document_folders as folders_mod
    client.post(SEED)
    monkeypatch.setattr(folders_mod, "plan_seed", lambda existing: ds.plan_seed([]))  # the second one planned from a stale list
    r = client.post(SEED)
    assert r.status_code == 409 and "already exists" in r.json()["detail"] and len(folders(db)) == TOTAL


def test_the_setup_is_recorded_in_the_history(client, db):
    client.post(SEED)
    entry = db.tables["document_activities"][-1]
    assert entry["action"] == "folder_seed" and entry["details"]["created"] == TOTAL
