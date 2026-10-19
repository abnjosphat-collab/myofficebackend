# tests/test_documents_search.py — the overview and the one search that serves every folder view and the universal search:
# what it finds, what it refuses, what it never sends, and that a hostile search cannot change the query.
import pytest

from tests._documents_client import U, make_client
from tests._documents_fake import FakeDb, doc, folder

FOLDERS = [
    folder(U(1), "Asset Management System (AMS)", None, seed_key="ams"),
    folder(U(2), "4. Support", U(1)),
    folder(U(3), "4.4 Documented Information", U(2)),
    folder(U(4), "SOPs / SWPs", U(3)),
    folder(U(5), "Loose folder", None),
]
DOCS = [
    doc(U(11), "Pump SOP.docx", ext="docx", file_type="document", folder_ref=U(4), folder_path="AMS > 4. Support", tags=["safety", "pumps"], file_size=2_000_000, created_at="2026-09-01T08:00:00+00:00"),
    doc(U(12), "Hot work permit.pdf", folder_ref=U(3), description="Permit for welding", starred=True, content_text="isolate the energy source before welding", file_size=500, created_at="2026-10-01T08:00:00+00:00", uploaded_by="Tafadzwa Matangira"),
    doc(U(13), "Site photo.jpg", ext="jpg", file_type="image", folder_ref=U(5), created_at="2026-10-10T08:00:00+00:00", file_size=3_000_000),
    doc(U(14), "Unfiled note.txt", ext="txt", file_type="text", folder_ref=None, created_at="2026-10-15T08:00:00+00:00"),
    doc(U(15), "Old budget.xlsx", ext="xlsx", file_type="spreadsheet", folder_ref=U(5), deleted_at="2026-10-16T08:00:00+00:00", deleted_by="Edson Mavhondo"),
]


@pytest.fixture
def db():
    return FakeDb({"documents": DOCS, "document_folders": FOLDERS})


@pytest.fixture
def client(monkeypatch, db):
    return make_client(monkeypatch, db)


def ids(response):
    return [r["id"] for r in response.json()["items"]]


def find(client, **params):
    r = client.get("/api/documents/search", params=params)
    assert r.status_code == 200, r.text
    return r


# ─── before the upgrade SQL ────────────────────────────────────────────────────────────────────────────────────────

def test_everything_says_what_to_do_until_the_upgrade_sql_has_run(monkeypatch, db):
    db.missing["documents"] = {"folder_ref"}
    client = make_client(monkeypatch, db)
    for r in (client.get("/api/documents/overview"), client.get("/api/documents/search"), client.post("/api/documents/folders", json={"name": "x"})):
        assert r.status_code == 409 and "one-time upgrade" in r.json()["detail"] and "supabase_migration_documents_v2.sql" in r.json()["detail"]


# ─── overview ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_overview_counts_files_in_each_folder_and_below(client):
    body = client.get("/api/documents/overview").json()
    counts = {f["id"]: (f["file_count"], f["total_count"]) for f in body["folders"]}
    assert counts[U(4)] == (1, 1) and counts[U(3)] == (1, 2) and counts[U(1)] == (0, 2)
    assert counts[U(5)] == (1, 1)  # the trashed file is not counted


def test_overview_counts_and_filter_options(client):
    body = client.get("/api/documents/overview").json()
    assert body["counts"] == {"all": 4, "unfiled": 1, "trashed": 1, "starred": 1, "unindexed": 0}
    assert body["facets"]["types"] == {"document": 1, "pdf": 1, "image": 1, "text": 1}
    assert {t["tag"] for t in body["facets"]["tags"]} == {"safety", "pumps"}
    assert {u["name"] for u in body["facets"]["uploaders"]} == {"Edson Mavhondo", "Tafadzwa Matangira"}
    assert body["seeded"] is True


def test_overview_says_when_the_ams_folders_are_not_set_up(monkeypatch):
    client = make_client(monkeypatch, FakeDb({"documents": [], "document_folders": [folder(U(9), "Mine")]}))
    body = client.get("/api/documents/overview").json()
    assert body["seeded"] is False and body["ams_missing"] == 85


def test_overview_counts_the_ams_folders_still_missing(client):
    # the fixture has the root, one section and two hand-made folders: the rest of the structure is still to create
    assert client.get("/api/documents/overview").json()["ams_missing"] >= 80


def test_overview_counts_files_whose_words_have_not_been_read(monkeypatch):
    unread = doc(U(31), "Old.docx", text_indexed_at=None)
    deleted_unread = doc(U(32), "Gone.docx", text_indexed_at=None, deleted_at="2026-10-16T08:00:00+00:00")
    client = make_client(monkeypatch, FakeDb({"documents": [unread, deleted_unread, doc(U(33))], "document_folders": []}))
    assert client.get("/api/documents/overview").json()["counts"]["unindexed"] == 1


def test_a_failed_load_is_an_error_not_an_empty_library(monkeypatch, db):
    client = make_client(monkeypatch, db)
    db.fail_ops = {"select"}
    # the column check is remembered, so only the real read fails
    r = client.get("/api/documents/overview")
    assert r.status_code == 500 and "ref " in r.json()["detail"] and "database unavailable" not in r.json()["detail"]


# ─── what a search finds ───────────────────────────────────────────────────────────────────────────────────────────

def test_with_no_words_it_lists_the_newest_live_files_first(client):
    assert ids(find(client)) == [U(14), U(13), U(12), U(11)]


def test_it_finds_by_name_case_insensitively(client):
    assert ids(find(client, q="PUMP")) == [U(11)]


def test_every_word_must_be_found(client):
    assert ids(find(client, q="pump sop")) == [U(11)]
    assert ids(find(client, q="pump photo")) == []


def test_a_quoted_phrase_must_match_as_written(client):
    assert ids(find(client, q='"hot work"')) == [U(12)]
    assert ids(find(client, q='"work hot"')) == []


def test_it_finds_by_tag_comment_folder_and_uploader(client):
    assert ids(find(client, q="pumps")) == [U(11)]
    assert ids(find(client, q="welding")) == [U(12)]            # the comment
    assert ids(find(client, q="matangira")) == [U(12)]          # who uploaded it


def test_it_finds_a_file_by_what_is_written_inside_it_and_says_so(client):
    r = find(client, q="isolate energy")
    assert ids(r) == [U(12)]
    assert r.json()["items"][0]["found_inside"] is True
    assert find(client, q="permit").json()["items"][0]["found_inside"] is False


def test_the_words_inside_a_file_are_never_sent_to_the_browser(client):
    item = find(client, q="isolate").json()["items"][0]
    for hidden in ("content_text", "search_text", "content_hash", "storage_path", "category_id", "folder_id"):
        assert hidden not in item


def test_a_name_match_ranks_above_a_match_inside_the_text(client, db):
    db.tables["documents"].append(doc(U(16), "Welding register.pdf", created_at="2026-01-01T08:00:00+00:00"))
    assert ids(find(client, q="welding"))[:2] == [U(16), U(12)]  # name first, then the comment, although it is older


def test_a_search_can_be_limited_to_names_only(client):
    assert ids(find(client, q="isolate", names_only="true")) == []


# ─── a search cannot be turned into another query ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hostile", ['x),name.eq.secret,(y', '"; drop table documents; --', "a,b)", 'quote"inside', "back\\slash", "100%", "a_b", "*", "%"])
def test_hostile_text_is_just_text(client, hostile):
    r = client.get("/api/documents/search", params={"q": hostile})
    assert r.status_code == 200 and r.json()["items"] == []


def test_percent_and_underscore_match_themselves_not_anything(client, db):
    db.tables["documents"].append(doc(U(17), "Progress 100% done.pdf"))
    db.tables["documents"].append(doc(U(18), "Progress 1000 done.pdf"))
    assert ids(find(client, q="100%")) == [U(17)]


# ─── filters ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_folder_scope_includes_subfolders_unless_told_not_to(client):
    assert set(ids(find(client, folder_id=U(3)))) == {U(11), U(12)}
    assert ids(find(client, folder_id=U(3), recursive="false")) == [U(12)]


def test_unfiled_and_trash_are_their_own_views(client):
    assert ids(find(client, unfiled="true")) == [U(14)]
    assert ids(find(client, trashed="true")) == [U(15)]
    assert U(15) not in ids(find(client))


def test_filters_by_type_extension_star_tag_uploader(client):
    assert ids(find(client, types="image,text")) == [U(14), U(13)]
    assert ids(find(client, types="nonsense")) == [U(14), U(13), U(12), U(11)]  # an unknown kind is ignored, not an error
    assert ids(find(client, exts=".docx,xlsx")) == [U(11)]
    assert ids(find(client, starred="true")) == [U(12)]
    assert ids(find(client, tags="safety,pumps")) == [U(11)]
    assert ids(find(client, tags="safety,missing")) == []
    assert ids(find(client, uploader="tafadzwa")) == [U(12)]


def test_filters_by_date_and_size(client):
    assert ids(find(client, added_from="2026-10-01", added_to="2026-10-10")) == [U(13), U(12)]
    assert ids(find(client, min_size=1_000_000)) == [U(13), U(11)]
    assert ids(find(client, max_size=600)) == [U(12)]


def test_filters_combine(client):
    assert ids(find(client, folder_id=U(1), types="pdf", starred="true", q="permit")) == [U(12)]


@pytest.mark.parametrize("params", [{"added_from": "yesterday"}, {"added_to": "2026-13"}, {"limit": 501}, {"limit": 0}, {"min_size": -1}])
def test_bad_input_is_refused_with_422(client, params):
    assert client.get("/api/documents/search", params=params).status_code == 422


def test_a_folder_that_is_not_a_uuid_is_refused(client):
    assert client.get("/api/documents/search", params={"folder_id": "SOPs"}).status_code == 422


# ─── ordering and size of the answer ───────────────────────────────────────────────────────────────────────────────

def test_sorting(client):
    assert ids(find(client, sort="name", dir="asc")) == [U(12), U(11), U(13), U(14)]
    assert ids(find(client, sort="name", dir="desc")) == [U(14), U(13), U(11), U(12)]
    assert ids(find(client, sort="size")) == [U(13), U(11), U(14), U(12)]  # largest first
    assert ids(find(client, sort="date", dir="asc")) == [U(11), U(12), U(13), U(14)]


def test_a_cut_off_answer_says_so_with_the_real_total(client):
    body = find(client, limit=2).json()
    assert len(body["items"]) == 2 and body["total"] == 4 and body["truncated"] is True
    assert find(client).json()["truncated"] is False


def test_the_terms_it_understood_are_returned_for_highlighting(client):
    assert find(client, q='"hot work" Permit').json()["terms"] == ["hot work", "permit"]
