# tests/test_documents_print_copy.py — Quick print: a Word or Excel paper keeps a PDF print copy so Print sends the exact
# pages. The copy is a real PDF, replaces the one before it, leaves with the file, and the Quick print folder cannot be deleted.
import pytest

from tests._documents_client import U, make_client
from tests._documents_fake import FakeDb, doc, folder

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
FOLDERS = [folder(U(1), "Quick print", None, seed_key="quick-print", sort_order=0), folder(U(2), "Reports", None)]


@pytest.fixture
def db():
    return FakeDb({
        "documents": [
            doc(U(11), "Daily checklist.docx", ext="docx", file_type="document", folder_ref=U(1), storage_path=f"x/{U(11)}.docx", content_hash="h1"),
            doc(U(12), "Permit.pdf", folder_ref=U(1), content_hash="h2"),
        ],
        "document_folders": FOLDERS, "document_activities": [],
    })


@pytest.fixture
def client(monkeypatch, db):
    return make_client(monkeypatch, db, role="user")


def add_copy(client, doc_id=U(11), content=PDF, filename="Daily checklist.pdf"):
    return client.post(f"/api/documents/{doc_id}/print-copy", files={"file": (filename, content, "application/pdf")})


def row(db, doc_id=U(11)):
    return next(d for d in db.tables["documents"] if d["id"] == doc_id)


def test_a_pdf_print_copy_is_stored_with_the_paper(client, db):
    r = add_copy(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["print_copy_name"] == "Daily checklist.pdf" and body["print_copy_size"] == len(PDF)
    assert body["print_copy_url"].startswith("https://files.test/print-copies/")
    assert "print_copy_path" not in body  # the storage path stays on the server
    stored = row(db)["print_copy_path"]
    assert db.files[stored]["options"]["content-type"] == "application/pdf"


def test_a_file_that_is_not_a_pdf_is_refused(client, db):
    assert add_copy(client, content=b"PK\x03\x04 not a pdf", filename="sneaky.pdf").status_code == 422
    assert add_copy(client, content=PDF, filename="copy.docx").status_code == 400
    assert row(db).get("print_copy_path") is None and all(not p.startswith("print-copies/") for p in db.files)


def test_a_pdf_needs_no_print_copy(client):
    r = add_copy(client, doc_id=U(12))
    assert r.status_code == 422 and "already a PDF" in r.json()["detail"]


def test_a_new_copy_replaces_the_old_one_in_storage(client, db):
    add_copy(client)
    first = row(db)["print_copy_path"]
    add_copy(client, filename="v2.pdf")
    assert row(db)["print_copy_name"] == "v2.pdf" and row(db)["print_copy_path"] != first and first not in db.files


def test_removing_the_copy_clears_it_and_its_stored_file(client, db):
    add_copy(client)
    stored = row(db)["print_copy_path"]
    r = client.delete(f"/api/documents/{U(11)}/print-copy")
    assert r.status_code == 200 and r.json()["print_copy_url"] is None
    assert row(db)["print_copy_path"] is None and stored not in db.files


def test_a_paper_in_the_trash_takes_no_copy(client, db):
    row(db)["deleted_at"] = "2026-10-29T08:00:00+00:00"
    assert add_copy(client).status_code == 409


def test_purging_a_paper_removes_its_print_copy_too(monkeypatch, client, db):
    add_copy(client)
    stored = row(db)["print_copy_path"]
    db.files[row(db)["storage_path"]] = {"content": b"x", "options": {}}
    manager = make_client(monkeypatch, db, role="manager")
    manager.post("/api/documents/bulk", json={"ids": [U(11)], "action": "trash"})
    out = manager.post("/api/documents/bulk", json={"ids": [U(11)], "action": "purge"}).json()
    assert out["done"] == [U(11)] and stored not in db.files and f"x/{U(11)}.docx" not in db.files


def test_the_quick_print_folder_cannot_be_deleted_even_when_empty(monkeypatch, db):
    db.tables["documents"] = []
    manager = make_client(monkeypatch, db, role="manager")
    r = manager.delete(f"/api/documents/folders/{U(1)}")
    assert r.status_code == 409 and "Quick print" in r.json()["detail"]
    assert manager.delete(f"/api/documents/folders/{U(2)}").status_code == 200


def test_until_the_quick_print_sql_has_run_the_library_names_that_file(monkeypatch, db):
    # has_columns remembers a yes for the life of the process: start from an empty memory so this probe really asks.
    from app import audit
    monkeypatch.setattr(audit, "_COLUMN_PROBE", {})
    db.missing["documents"] = {"print_copy_path"}
    client = make_client(monkeypatch, db)
    r = client.get("/api/documents/overview")
    assert r.status_code == 409 and "supabase_migration_documents_quick_print_2026-10-29.sql" in r.json()["detail"]
