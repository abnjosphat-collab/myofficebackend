# tests/test_documents_files.py — putting files in, changing them, moving them, and taking them out: the rules that make a
# file hard to lose and impossible to put in twice.
import io
import zipfile

import pytest

from tests._documents_client import USER_ID, U, make_client
from tests._documents_fake import FakeDb, doc, folder

FOLDERS = [
    folder(U(1), "Asset Management System (AMS)", None, seed_key="ams"),
    folder(U(2), "4. Support", U(1)),
    folder(U(3), "Reports", U(2)),
    folder(U(4), "Archive", U(1)),
]


def docx(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:p><w:t>{text}</w:t></w:p>")
    return buf.getvalue()


@pytest.fixture
def db():
    return FakeDb({"documents": [], "document_folders": FOLDERS, "document_activities": []})


@pytest.fixture
def client(monkeypatch, db):
    return make_client(monkeypatch, db)


def upload(client, content=b"hello", filename="Notes.txt", folder_id="", content_type="text/plain", **form):
    return client.post("/api/documents/upload", files={"file": (filename, content, content_type)}, data={"folder_id": folder_id, **form})


def rows(db):
    return db.tables["documents"]


def history(db, action=None):
    return [a for a in db.tables["document_activities"] if action is None or a["action"] == action]


# ─── upload ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_an_upload_is_stored_and_described_from_the_sign_in(client, db):
    r = upload(client, docx("Lock out tag out"), "LOTO.docx", U(3), name="LOTO procedure", tags="Safety, safety ,Procedures", description="  v2 ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "LOTO procedure" and body["original_name"] == "LOTO.docx" and body["ext"] == "docx" and body["file_type"] == "document"
    assert body["folder_ref"] == U(3) and body["folder_path"] == "Asset Management System (AMS) > 4. Support > Reports"
    assert body["tags"] == ["safety", "procedures"] and body["description"] == "v2"
    assert body["uploaded_by"] == "Edson Mavhondo"  # the signed-in person, not anything the browser said
    assert body["file_url"].startswith("https://files.test/")
    saved = rows(db)[0]
    assert "Lock out tag out" in saved["content_text"] and len(saved["content_hash"]) == 64 and saved["text_indexed_at"]
    assert len(db.files) == 1 and body["storage_path" if False else "id"] == saved["id"]


def test_the_response_does_not_carry_the_file_text_or_hash(client):
    body = upload(client, docx("secret words"), "a.docx").json()
    for hidden in ("content_text", "search_text", "content_hash", "storage_path"):
        assert hidden not in body


def test_who_uploaded_cannot_be_sent_by_the_browser(client, db):
    upload(client, b"x", "a.txt", uploaded_by="Someone Else")
    assert rows(db)[0]["uploaded_by"] == "Edson Mavhondo"


def test_the_served_type_follows_the_extension_not_the_browsers_claim(client, db):
    upload(client, b"<script>alert(1)</script>", "page.txt", content_type="text/html")
    assert next(iter(db.files.values()))["options"]["content-type"] == "text/plain"
    assert rows(db)[0]["mime_type"] == "text/plain"


def test_a_file_can_be_left_unfiled(client, db):
    body = upload(client, b"x", "loose.txt").json()
    assert body["folder_ref"] is None and body["folder_path"] == "" and next(iter(db.files)).startswith("unfiled/")


@pytest.mark.parametrize("name,kind", [("p.png", "image"), ("v.m4a", "audio"), ("c.mp4", "video"), ("m.eml", "email"), ("d.dwg", "drawing"), ("s.csv", "spreadsheet"), ("x.pptx", "presentation"), ("a.heic", "image")])
def test_the_kinds_of_file_that_were_asked_for_are_accepted(client, name, kind):
    r = upload(client, b"data", name, content_type="application/octet-stream")
    assert r.status_code == 200 and r.json()["file_type"] == kind


@pytest.mark.parametrize("name", ["virus.exe", "run.bat", "page.html", "script.js", "noextension", "x.ps1"])
def test_dangerous_or_unknown_types_are_refused(client, db, name):
    r = upload(client, b"data", name)
    assert r.status_code == 400 and "Unsupported file type" in r.json()["detail"] and rows(db) == [] and db.files == {}


def test_an_empty_file_is_refused(client):
    assert upload(client, b"", "a.txt").status_code == 400


def test_a_path_in_the_file_name_is_dropped(client, db):
    upload(client, b"x", "C:\\Users\\me\\Desktop\\plan.txt")
    assert rows(db)[0]["original_name"] == "plan.txt" and rows(db)[0]["name"] == "plan.txt"


def test_an_unknown_or_malformed_folder_is_refused_before_anything_is_stored(client, db):
    assert upload(client, b"x", "a.txt", U(99)).status_code == 404
    assert upload(client, b"x", "a.txt", "SOPs").status_code == 422
    assert rows(db) == [] and db.files == {}


def test_the_same_file_cannot_go_in_one_folder_twice(client, db):
    assert upload(client, b"same bytes", "first.txt", U(3)).status_code == 200
    r = upload(client, b"same bytes", "renamed.txt", U(3))
    assert r.status_code == 409 and "already in this folder as “first.txt”" in r.json()["detail"]
    assert len(rows(db)) == 1 and len(db.files) == 1


def test_the_same_file_in_another_folder_is_allowed_and_points_to_the_other_copy(client, db):
    first = upload(client, b"same bytes", "first.txt", U(3)).json()
    second = upload(client, b"same bytes", "first.txt", U(4))
    assert second.status_code == 200
    assert second.json()["also_in"] == [{"id": first["id"], "name": "first.txt", "folder_path": first["folder_path"]}]


def test_two_different_files_with_one_name_get_a_number(client):
    names = [upload(client, content, "Report.pdf", U(3), content_type="application/pdf").json()["name"] for content in (b"one", b"two", b"three")]
    assert names == ["Report.pdf", "Report (2).pdf", "Report (3).pdf"]


def test_too_many_or_too_long_tags_are_refused(client):
    assert upload(client, b"x", "a.txt", tags=",".join(f"t{i}" for i in range(21))).status_code == 422
    assert upload(client, b"x", "a.txt", tags="x" * 41).status_code == 422


def test_a_storage_failure_saves_nothing(client, db):
    db.fail_storage = {"upload"}
    r = upload(client, b"x", "a.txt")
    assert r.status_code == 500 and "ref " in r.json()["detail"] and rows(db) == []


def test_a_failed_save_does_not_leave_the_file_in_storage(client, db):
    db.fail_ops = {"insert"}
    r = upload(client, b"x", "a.txt")
    assert r.status_code == 500 and db.files == {}


def test_two_people_uploading_the_same_file_at_once_get_the_database_rule_not_a_crash(client, db, monkeypatch):
    import app.routers.documents as documents_mod
    upload(client, b"race", "a.txt", U(3))
    monkeypatch.setattr(documents_mod, "_copy_in", lambda *a, **k: None)  # the check passed for both; the unique index decides
    r = upload(client, b"race", "b.txt", U(3))
    assert r.status_code == 409 and "already in that folder" in r.json()["detail"]
    assert len(rows(db)) == 1 and len(db.files) == 1  # the loser's stored copy is cleaned up


def test_an_upload_is_written_to_the_history(client, db):
    upload(client, b"x", "a.txt", U(3))
    entry = history(db, "upload")[0]
    assert entry["details"]["by"] == "Edson Mavhondo" and entry["details"]["name"] == "a.txt" and entry["user_id"] == USER_ID


# ─── edit ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def put(client, id, **body):
    return client.put(f"/api/documents/{id}", json=body)


@pytest.fixture
def two(db):
    db.tables["documents"] += [
        doc(U(11), "A.pdf", folder_ref=U(3), folder_path="x", content_hash="h1"),
        doc(U(12), "B.pdf", folder_ref=U(3), folder_path="x", content_hash="h2"),
        doc(U(13), "A.pdf", folder_ref=U(4), folder_path="y", content_hash="h3"),
    ]


def test_rename_comment_star_and_tags_save(client, db, two):
    r = put(client, U(11), name="  Pump   SOP ", description="rev 3", starred=True, tags=["Safety", "safety", "Pumps"])
    assert r.status_code == 200
    saved = next(d for d in rows(db) if d["id"] == U(11))
    assert (saved["name"], saved["description"], saved["starred"], saved["tags"]) == ("Pump SOP", "rev 3", True, ["safety", "pumps"])
    assert saved["updated_at"] > "2026-10-19T08:00:00+00:00"


def test_renaming_to_a_name_already_in_the_folder_is_refused(client, two):
    r = put(client, U(12), name="a.PDF")
    assert r.status_code == 409 and "already in this folder" in r.json()["detail"]


def test_saving_the_same_name_back_is_fine(client, two):
    assert put(client, U(11), name="A.pdf").status_code == 200


def test_a_stale_edit_is_refused(client, db, two):
    r = put(client, U(11), name="Mine", expected_updated_at="2026-10-19T07:00:00+00:00")
    assert r.status_code == 409 and "Someone else changed" in r.json()["detail"]
    assert next(d for d in rows(db) if d["id"] == U(11))["name"] == "A.pdf"


def test_a_missing_or_malformed_id_is_a_404(client, two):
    assert put(client, U(99), name="x").status_code == 404
    assert put(client, "not-a-uuid", name="x").status_code == 404


def test_moving_changes_folder_and_path_and_is_in_the_history(client, db, two):
    r = put(client, U(12), folder_id=U(4))
    assert r.status_code == 200
    saved = next(d for d in rows(db) if d["id"] == U(12))
    assert saved["folder_ref"] == U(4) and saved["folder_path"] == "Asset Management System (AMS) > Archive"
    assert history(db, "move")[0]["details"]["to"] == saved["folder_path"]


def test_moving_into_a_folder_that_has_that_name_numbers_it(client, db, two):
    put(client, U(11), folder_id=U(4))  # "A.pdf" is already in Archive (the third file)
    assert next(d for d in rows(db) if d["id"] == U(11))["name"] == "A (2).pdf"


def test_moving_where_the_same_file_already_is_is_refused(client, db, two):
    next(d for d in rows(db) if d["id"] == U(13))["content_hash"] = "h1"
    r = put(client, U(11), folder_id=U(4))
    assert r.status_code == 409 and "already in that folder" in r.json()["detail"]
    assert next(d for d in rows(db) if d["id"] == U(11))["folder_ref"] == U(3)


def test_null_takes_a_file_out_of_its_folder_and_leaving_it_out_keeps_it(client, db, two):
    put(client, U(11), name="Renamed.pdf")
    assert next(d for d in rows(db) if d["id"] == U(11))["folder_ref"] == U(3)
    put(client, U(11), folder_id=None)
    saved = next(d for d in rows(db) if d["id"] == U(11))
    assert saved["folder_ref"] is None and saved["folder_path"] == ""


def test_moving_to_an_unknown_folder_is_refused(client, two):
    assert put(client, U(11), folder_id=U(99)).status_code == 404
    assert put(client, U(11), folder_id="SOPs").status_code == 422


def test_starring_is_not_history_but_a_rename_is(client, db, two):
    put(client, U(11), starred=True)
    assert history(db) == []
    put(client, U(11), name="New.pdf")
    assert history(db, "update")[0]["details"]["changed"] == ["name"]


# ─── bulk ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def bulk(client, ids, action, **extra):
    return client.post("/api/documents/bulk", json={"ids": ids, "action": action, **extra})


def test_star_and_unstar_many(client, db, two):
    r = bulk(client, [U(11), U(12)], "star")
    assert r.status_code == 200 and r.json() == {"done": [U(11), U(12)], "failed": []}
    assert all(d["starred"] for d in rows(db) if d["id"] in (U(11), U(12)))
    bulk(client, [U(11)], "unstar")
    assert not next(d for d in rows(db) if d["id"] == U(11))["starred"]


def test_add_and_remove_tags_on_many(client, db, two):
    bulk(client, [U(11), U(12)], "tag_add", tags=["Audit", "2026"])
    assert all(d["tags"] == ["audit", "2026"] for d in rows(db) if d["id"] in (U(11), U(12)))
    bulk(client, [U(11)], "tag_remove", tags=["audit"])
    assert next(d for d in rows(db) if d["id"] == U(11))["tags"] == ["2026"]


def test_move_many_reports_the_one_that_could_not_move(client, db, two):
    next(d for d in rows(db) if d["id"] == U(13))["content_hash"] = "h1"  # Archive already holds the same file as U(11)
    r = bulk(client, [U(11), U(12)], "move", folder_id=U(4)).json()
    assert r["done"] == [U(12)] and len(r["failed"]) == 1
    assert r["failed"][0]["id"] == U(11) and r["failed"][0]["name"] == "A.pdf" and "already in that folder" in r["failed"][0]["error"]


def test_trash_then_restore(client, db, two):
    r = bulk(client, [U(11), U(12)], "trash")
    assert r.json()["failed"] == []
    gone = next(d for d in rows(db) if d["id"] == U(11))
    assert gone["deleted_at"] and gone["deleted_by"] == "Edson Mavhondo"
    assert len(history(db, "trash")) == 2
    bulk(client, [U(11)], "restore")
    back = next(d for d in rows(db) if d["id"] == U(11))
    assert back["deleted_at"] is None and back["deleted_by"] == ""


def test_trashing_twice_and_restoring_a_live_file_are_reported_not_hidden(client, two):
    bulk(client, [U(11)], "trash")
    again = bulk(client, [U(11)], "trash").json()
    assert again["done"] == [] and "already in the trash" in again["failed"][0]["error"]
    live = bulk(client, [U(12)], "restore").json()
    assert "not in the trash" in live["failed"][0]["error"]


def test_restoring_where_an_identical_file_now_sits_is_reported(client, db, two):
    bulk(client, [U(11)], "trash")
    db.tables["documents"].append(doc(U(14), "Copy.pdf", folder_ref=U(3), content_hash="h1"))
    failed = bulk(client, [U(11)], "restore").json()["failed"]
    assert "already in that folder as “Copy.pdf”" in failed[0]["error"]


def test_restoring_a_name_that_has_since_been_taken_numbers_it(client, db, two):
    bulk(client, [U(11)], "trash")
    db.tables["documents"].append(doc(U(14), "A.pdf", folder_ref=U(3), content_hash="h9"))
    bulk(client, [U(11)], "restore")
    assert next(d for d in rows(db) if d["id"] == U(11))["name"] == "A (2).pdf"


def test_purge_only_works_from_the_trash_and_removes_the_stored_file(client, db, two):
    db.files[rows(db)[0]["storage_path"]] = {"content": b"x", "options": {}}
    live = bulk(client, [U(11)], "purge").json()
    assert "Move it to the trash first" in live["failed"][0]["error"] and len(rows(db)) == 3
    bulk(client, [U(11)], "trash")
    done = bulk(client, [U(11)], "purge").json()
    assert done["done"] == [U(11)] and all(d["id"] != U(11) for d in rows(db)) and db.files == {}
    assert history(db, "purge")[0]["details"]["name"] == "A.pdf"


def test_a_storage_failure_during_purge_keeps_the_row(client, db, two):
    bulk(client, [U(11)], "trash")
    db.fail_storage = {"remove"}
    out = bulk(client, [U(11)], "purge").json()
    assert out["done"] == [] and "nothing was deleted" in out["failed"][0]["error"] and any(d["id"] == U(11) for d in rows(db))


def test_deleting_is_for_managers_only(monkeypatch, db, two):
    staff = make_client(monkeypatch, db, role="user")
    assert bulk(staff, [U(11)], "trash").status_code == 403
    assert bulk(staff, [U(11)], "purge").status_code == 403
    assert staff.delete(f"/api/documents/{U(11)}").status_code == 403
    assert bulk(staff, [U(11)], "star").status_code == 200  # everything else is open to signed-in staff
    assert bulk(staff, [U(11)], "tag_add", tags=["x"]).status_code == 200
    assert not any(d["deleted_at"] for d in rows(db))


def test_bulk_input_is_checked(client, two):
    assert bulk(client, [], "star").status_code == 422
    assert bulk(client, ["nope"], "star").status_code == 422
    assert bulk(client, [U(11)], "explode").status_code == 422
    assert bulk(client, [U(i) for i in range(1, 202)], "star").status_code == 422
    assert bulk(client, [U(11)], "tag_add", tags=["x" * 41]).status_code == 422


def test_a_file_that_vanished_is_reported_and_the_rest_still_go_through(client, two):
    out = bulk(client, [U(99), U(11)], "star").json()
    assert out["done"] == [U(11)] and out["failed"] == [{"id": U(99), "error": "It no longer exists."}]


def test_a_duplicate_id_is_applied_once(client, db, two):
    assert bulk(client, [U(11), U(11)], "trash").json()["done"] == [U(11)]
    assert len(history(db, "trash")) == 1


# ─── delete one ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_delete_moves_to_the_trash_and_keeps_the_file(client, db, two):
    db.files["x/" + U(11) + ".pdf"] = {"content": b"x", "options": {}}
    assert client.delete(f"/api/documents/{U(11)}").status_code == 200
    saved = next(d for d in rows(db) if d["id"] == U(11))
    assert saved["deleted_at"] and len(db.files) == 1


def test_permanent_delete_needs_the_trash_first_then_removes_everything(client, db, two):
    db.files["x/" + U(11) + ".pdf"] = {"content": b"x", "options": {}}
    r = client.delete(f"/api/documents/{U(11)}", params={"permanent": "true"})
    assert r.status_code == 409 and "trash first" in r.json()["detail"]
    client.delete(f"/api/documents/{U(11)}")
    assert client.delete(f"/api/documents/{U(11)}", params={"permanent": "true"}).status_code == 200
    assert all(d["id"] != U(11) for d in rows(db)) and db.files == {}


def test_deleting_something_that_is_gone_is_a_404(client, two):
    assert client.delete(f"/api/documents/{U(99)}").status_code == 404


# ─── history and indexing ──────────────────────────────────────────────────────────────────────────────────────────

def test_the_history_of_one_file_newest_first(client, db, two):
    put(client, U(11), name="One.pdf")
    put(client, U(11), name="Two.pdf")
    db.tables["document_activities"][0]["created_at"] = "2026-10-19T09:00:00+00:00"
    db.tables["document_activities"][1]["created_at"] = "2026-10-19T10:00:00+00:00"
    entries = client.get(f"/api/documents/{U(11)}/activity").json()
    assert [e["details"]["name"] for e in entries] == ["Two.pdf", "One.pdf"]
    assert client.get("/api/documents/nope/activity").status_code == 404


def test_old_files_are_read_a_few_at_a_time_and_marked_done_even_when_unreadable(client, db):
    db.tables["documents"] += [
        doc(U(21), "Old.docx", ext="docx", storage_path="old/a.docx", text_indexed_at=None, file_size=100),
        doc(U(22), "Scan.pdf", ext="pdf", storage_path="old/b.pdf", text_indexed_at=None, file_size=100),
        doc(U(23), "Third.txt", ext="txt", storage_path="old/c.txt", text_indexed_at=None, file_size=100),
    ]
    db.files["old/a.docx"] = {"content": docx("kiln alignment"), "options": {}}
    db.files["old/b.pdf"] = {"content": b"not a pdf", "options": {}}
    db.files["old/c.txt"] = {"content": b"plain words", "options": {}}
    first = client.post("/api/documents/reindex", params={"limit": 2}).json()
    assert first == {"processed": 2, "remaining": 1}
    old = next(d for d in rows(db) if d["id"] == U(21))
    assert "kiln alignment" in old["content_text"] and old["text_indexed_at"]
    assert next(d for d in rows(db) if d["id"] == U(22))["content_text"] == ""  # unreadable, but not retried forever
    assert client.post("/api/documents/reindex", params={"limit": 2}).json() == {"processed": 1, "remaining": 0}
    assert client.post("/api/documents/reindex").json() == {"processed": 0, "remaining": 0}


def test_reindexing_is_for_managers(monkeypatch, db):
    assert make_client(monkeypatch, db, role="user").post("/api/documents/reindex").status_code == 403
