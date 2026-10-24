# tests/test_scope_batch5.py — Batch 5: the document library is a subtree per
# scope. A scoped viewer sees the top-level folder named for their scope and
# everything under it; unfiled files, other trees (even a nested folder with
# the same name) and the whole-library jobs are out of reach.
import pytest

from tests.scope_harness import SCOPED, SCOPED_MGR, UNSCOPED, employees_store, mount_router

P_ROOT = "11111111-1111-4111-8111-111111111111"
P_SUB = "22222222-2222-4222-8222-222222222222"
P_EMPTY = "55555555-5555-4555-8555-555555555555"
M_ROOT = "33333333-3333-4333-8333-333333333333"
M_PROJ = "44444444-4444-4444-8444-444444444444"

D1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
D2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
D3 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3"
D4 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4"
D5 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5"
D6 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa6"


def _folder(fid, name, parent, order=1000):
    return {"id": fid, "parent_id": parent, "name": name, "sort_order": order,
            "seed_key": None, "description": "", "created_by": "admin",
            "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"}


def _doc(did, name, folder_ref, folder_path, deleted_at=None):
    return {"id": did, "name": name, "original_name": name, "file_url": f"https://cdn.test/{did}",
            "file_size": 100, "mime_type": "text/plain", "file_type": "text", "ext": "txt",
            "folder_ref": folder_ref, "folder_path": folder_path, "description": "",
            "tags": ["pump"], "starred": False, "content_hash": f"hash-{did[-2:]}",
            "storage_path": f"{folder_ref or 'unfiled'}/{did}.txt",
            "content_text": f"words inside {name}", "search_text": name.lower(),
            "uploaded_by": "Ann", "deleted_at": deleted_at, "deleted_by": "",
            "text_indexed_at": "2026-10-01T00:00:00+00:00",
            "created_at": "2026-10-01T00:00:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00"}


def _store():
    return {
        "document_folders": [
            _folder(P_ROOT, "Projects", None),
            _folder(P_SUB, "Drawings", P_ROOT),
            _folder(P_EMPTY, "Empty", P_ROOT),
            _folder(M_ROOT, "Mechanical", None),
            _folder(M_PROJ, "Projects", M_ROOT),
        ],
        "documents": [
            _doc(D1, "plan.txt", P_ROOT, "Projects"),
            _doc(D2, "drawing.txt", P_SUB, "Projects / Drawings"),
            _doc(D3, "mech.txt", M_ROOT, "Mechanical"),
            _doc(D4, "nested.txt", M_PROJ, "Mechanical / Projects"),
            _doc(D5, "loose.txt", None, ""),
            _doc(D6, "trashed.txt", P_ROOT, "Projects", deleted_at="2026-10-10T00:00:00+00:00"),
        ],
        "document_activities": [
            {"id": "a1", "document_id": D1, "action": "upload",
             "details": {"by": "Ann"}, "created_at": "2026-10-01T00:00:00+00:00"},
            {"id": "a2", "document_id": D3, "action": "upload",
             "details": {"by": "Mike"}, "created_at": "2026-10-01T00:00:00+00:00"},
        ],
    }


class _Bucket:
    def __init__(self):
        self.uploaded = {}

    def upload(self, path, content, opts=None):
        self.uploaded[path] = content
        return {"path": path}

    def get_public_url(self, path):
        return f"https://cdn.test/{path}"

    def remove(self, paths):
        for p in paths:
            self.uploaded.pop(p, None)


class _Storage:
    def __init__(self):
        self.buckets = {}

    def from_(self, bucket):
        return self.buckets.setdefault(bucket, _Bucket())


@pytest.fixture
def docs_client(monkeypatch):
    import app.routers.documents as mod
    client = mount_router(monkeypatch, mod, "/api/documents", _store())
    monkeypatch.setattr(mod.supabase, "storage", _Storage(), raising=False)
    return client


@pytest.fixture
def folders_client(monkeypatch):
    import app.routers.document_folders as mod
    return mount_router(monkeypatch, mod, "/api/documents", _store())


@pytest.fixture
def noroot_client(monkeypatch):
    """A library where nobody has made the Projects root yet."""
    import app.routers.document_folders as mod
    store = _store()
    store["document_folders"] = [f for f in store["document_folders"] if f["id"] == M_ROOT]
    store["documents"] = [d for d in store["documents"] if d["id"] == D3]
    return mount_router(monkeypatch, mod, "/api/documents", store)


# ─── overview ───────────────────────────────────────────────────────────────

def test_overview_scoped_sees_the_subtree_only(docs_client):
    docs_client.as_user(SCOPED)
    body = docs_client.get("/api/documents/overview").json()
    assert sorted(f["name"] for f in body["folders"]) == ["Drawings", "Empty", "Projects"]
    assert body["counts"]["all"] == 2
    assert body["counts"]["trashed"] == 1
    assert body["counts"]["unfiled"] == 0
    assert body["seeded"] is True
    assert body["ams_missing"] == 0


def test_overview_unscoped_sees_everything(docs_client):
    docs_client.as_user(UNSCOPED)
    body = docs_client.get("/api/documents/overview").json()
    assert len(body["folders"]) == 5
    assert body["counts"]["all"] == 5
    assert body["counts"]["unfiled"] == 1


# ─── search ─────────────────────────────────────────────────────────────────

def test_search_scoped_global_stays_in_subtree(docs_client):
    docs_client.as_user(SCOPED)
    body = docs_client.get("/api/documents/search", params={"q": "txt"}).json()
    assert sorted(d["id"] for d in body["items"]) == [D1, D2]
    assert body["total"] == 2


def test_search_scoped_folder_outside_is_404(docs_client):
    docs_client.as_user(SCOPED)
    assert docs_client.get("/api/documents/search", params={"folder_id": M_ROOT}).status_code == 404


def test_search_scoped_folder_inside_returns_its_files(docs_client):
    docs_client.as_user(SCOPED)
    body = docs_client.get("/api/documents/search", params={"folder_id": P_SUB}).json()
    assert [d["id"] for d in body["items"]] == [D2]


def test_search_scoped_unfiled_is_empty(docs_client):
    docs_client.as_user(SCOPED)
    body = docs_client.get("/api/documents/search", params={"unfiled": "true"}).json()
    assert body["items"] == [] and body["total"] == 0
    docs_client.as_user(UNSCOPED)
    body = docs_client.get("/api/documents/search", params={"unfiled": "true"}).json()
    assert [d["id"] for d in body["items"]] == [D5]


def test_search_scoped_trash_stays_in_subtree(docs_client):
    docs_client.as_user(SCOPED)
    body = docs_client.get("/api/documents/search", params={"trashed": "true"}).json()
    assert [d["id"] for d in body["items"]] == [D6]


# ─── upload ─────────────────────────────────────────────────────────────────

def _upload(client, folder_id, name="memo.txt", content=b"hello projects"):
    data = {}
    if folder_id is not None:
        data["folder_id"] = folder_id
    return client.post("/api/documents/upload",
                       files={"file": (name, content, "text/plain")}, data=data)


def test_upload_scoped_into_subtree_files_it(docs_client):
    docs_client.as_user(SCOPED)
    resp = _upload(docs_client, P_SUB)
    assert resp.status_code == 200, resp.text
    saved = docs_client.scope_store["documents"][-1]
    assert saved["folder_ref"] == P_SUB
    assert saved["uploaded_by"] == "scoped@example.com"


def test_upload_scoped_elsewhere_writes_nothing(docs_client):
    import app.routers.documents as mod
    docs_client.as_user(SCOPED)
    before = len(docs_client.scope_store["documents"])
    assert _upload(docs_client, M_ROOT).status_code == 404
    assert _upload(docs_client, None).status_code == 404
    assert len(docs_client.scope_store["documents"]) == before
    bucket = mod.supabase.storage.buckets.get("ams-documents")
    assert not bucket or not bucket.uploaded


def test_upload_also_in_names_subtree_only(docs_client):
    import hashlib
    docs_client.as_user(SCOPED)
    content = b"shared content"
    digest = hashlib.sha256(content).hexdigest()
    docs_client.scope_store["documents"][2]["content_hash"] = digest  # D3, Mechanical
    body = _upload(docs_client, P_SUB, name="copy.txt", content=content).json()
    assert body["also_in"] == []


# ─── update / move ──────────────────────────────────────────────────────────

def test_update_scoped_outside_doc_is_404(docs_client):
    docs_client.as_user(SCOPED)
    resp = docs_client.put(f"/api/documents/{D3}", json={"description": "sneaky"})
    assert resp.status_code == 404
    assert docs_client.scope_store["documents"][2]["description"] == ""


def test_update_scoped_own_doc_renames(docs_client):
    docs_client.as_user(SCOPED)
    resp = docs_client.put(f"/api/documents/{D1}", json={"description": "edited"})
    assert resp.status_code == 200
    assert docs_client.scope_store["documents"][0]["description"] == "edited"


def test_move_scoped_out_writes_nothing(docs_client):
    docs_client.as_user(SCOPED)
    resp = docs_client.put(f"/api/documents/{D1}", json={"folder_id": M_ROOT})
    assert resp.status_code == 404
    assert docs_client.scope_store["documents"][0]["folder_ref"] == P_ROOT


def test_move_scoped_to_unfiled_is_404(docs_client):
    docs_client.as_user(SCOPED)
    resp = docs_client.put(f"/api/documents/{D1}", json={"folder_id": None})
    assert resp.status_code == 404
    assert docs_client.scope_store["documents"][0]["folder_ref"] == P_ROOT


def test_move_scoped_within_subtree_lands(docs_client):
    docs_client.as_user(SCOPED)
    resp = docs_client.put(f"/api/documents/{D1}", json={"folder_id": P_SUB})
    assert resp.status_code == 200, resp.text
    assert docs_client.scope_store["documents"][0]["folder_ref"] == P_SUB


# ─── bulk ───────────────────────────────────────────────────────────────────

def test_bulk_scoped_reports_outside_files_as_missing(docs_client):
    docs_client.as_user(SCOPED)
    body = docs_client.post("/api/documents/bulk", json={"ids": [D1, D3], "action": "star"}).json()
    assert body["done"] == [D1]
    assert [(f["id"], f["error"]) for f in body["failed"]] == [(D3, "It no longer exists.")]
    assert docs_client.scope_store["documents"][0]["starred"] is True
    assert docs_client.scope_store["documents"][2]["starred"] is False


def test_bulk_move_scoped_to_outside_is_404_upfront(docs_client):
    docs_client.as_user(SCOPED)
    resp = docs_client.post("/api/documents/bulk", json={"ids": [D1], "action": "move", "folder_id": M_ROOT})
    assert resp.status_code == 404
    assert docs_client.scope_store["documents"][0]["folder_ref"] == P_ROOT


def test_bulk_move_scoped_within_splits_done_failed(docs_client):
    docs_client.as_user(SCOPED)
    body = docs_client.post(
        "/api/documents/bulk", json={"ids": [D1, D3], "action": "move", "folder_id": P_SUB}).json()
    assert body["done"] == [D1]
    assert [f["id"] for f in body["failed"]] == [D3]


# ─── delete / history / whole-library jobs ──────────────────────────────────

def test_delete_scoped_outside_doc_is_404(docs_client):
    docs_client.as_user(SCOPED_MGR)
    assert docs_client.delete(f"/api/documents/{D3}").status_code == 404
    assert docs_client.scope_store["documents"][2]["deleted_at"] is None


def test_delete_scoped_own_doc_trashes(docs_client):
    docs_client.as_user(SCOPED_MGR)
    assert docs_client.delete(f"/api/documents/{D1}").status_code == 200
    assert docs_client.scope_store["documents"][0]["deleted_at"] is not None


def test_activity_scoped_follows_the_document(docs_client):
    docs_client.as_user(SCOPED)
    assert docs_client.get(f"/api/documents/{D3}/activity").status_code == 404
    assert docs_client.get(f"/api/documents/{D1}/activity").status_code == 200


def test_reindex_scoped_manager_is_refused(docs_client):
    docs_client.as_user(SCOPED_MGR)
    resp = docs_client.post("/api/documents/reindex")
    assert resp.status_code == 403
    assert "section-limited" in resp.json()["detail"]


def test_seed_scoped_manager_is_refused(folders_client):
    folders_client.as_user(SCOPED_MGR)
    resp = folders_client.post("/api/documents/folders/seed-ams")
    assert resp.status_code == 403
    assert "section-limited" in resp.json()["detail"]


# ─── folders ────────────────────────────────────────────────────────────────

def test_folder_create_scoped_under_subtree(folders_client):
    folders_client.as_user(SCOPED)
    resp = folders_client.post("/api/documents/folders", json={"parent_id": P_ROOT, "name": "Plans"})
    assert resp.status_code == 200, resp.text
    assert folders_client.scope_store["document_folders"][-1]["parent_id"] == P_ROOT


def test_folder_create_scoped_outside_writes_nothing(folders_client):
    folders_client.as_user(SCOPED)
    before = len(folders_client.scope_store["document_folders"])
    assert folders_client.post(
        "/api/documents/folders", json={"parent_id": M_ROOT, "name": "Plans"}).status_code == 404
    assert folders_client.post(
        "/api/documents/folders", json={"name": "Field Notes"}).status_code == 404
    assert len(folders_client.scope_store["document_folders"]) == before


def test_folder_create_scoped_self_service_root(noroot_client):
    noroot_client.as_user(SCOPED)
    resp = noroot_client.post("/api/documents/folders", json={"name": "Projects"})
    assert resp.status_code == 200, resp.text
    made = noroot_client.scope_store["document_folders"][-1]
    assert made["parent_id"] is None


def test_folder_rename_root_away_is_404(folders_client):
    folders_client.as_user(SCOPED)
    resp = folders_client.put(f"/api/documents/folders/{P_ROOT}", json={"name": "Old"})
    assert resp.status_code == 404
    root = next(f for f in folders_client.scope_store["document_folders"] if f["id"] == P_ROOT)
    assert root["name"] == "Projects"


def test_folder_move_scoped_out_is_404(folders_client):
    folders_client.as_user(SCOPED)
    resp = folders_client.put(f"/api/documents/folders/{P_SUB}", json={"parent_id": M_ROOT})
    assert resp.status_code == 404
    sub = next(f for f in folders_client.scope_store["document_folders"] if f["id"] == P_SUB)
    assert sub["parent_id"] == P_ROOT


def test_folder_move_root_is_404(folders_client):
    folders_client.as_user(SCOPED)
    resp = folders_client.put(f"/api/documents/folders/{P_ROOT}", json={"parent_id": P_SUB})
    assert resp.status_code == 404


def test_folder_update_scoped_outside_is_404(folders_client):
    folders_client.as_user(SCOPED)
    resp = folders_client.put(f"/api/documents/folders/{M_ROOT}", json={"description": "x"})
    assert resp.status_code == 404


def test_folder_delete_scoped_outside_is_404(folders_client):
    folders_client.as_user(SCOPED_MGR)
    assert folders_client.delete(f"/api/documents/folders/{M_ROOT}").status_code == 404
    assert any(f["id"] == M_ROOT for f in folders_client.scope_store["document_folders"])


def test_folder_delete_scoped_empty_inside_deletes(folders_client):
    folders_client.as_user(SCOPED_MGR)
    assert folders_client.delete(f"/api/documents/folders/{P_EMPTY}").status_code == 200
    assert all(f["id"] != P_EMPTY for f in folders_client.scope_store["document_folders"])


# ─── summaries sweep: open stats slice for scoped sign-ins ──────────────────

@pytest.fixture
def leaves_client(monkeypatch):
    import app.routers.leaves as mod
    store = {
        "employees": employees_store(),
        "leaves": [
            {"id": 1, "employee_id": "C1001", "status": "approved",
             "start_date": "2026-01-01", "end_date": "2026-01-05"},
            {"id": 2, "employee_id": "C2001", "status": "approved",
             "start_date": "2026-01-01", "end_date": "2026-01-05"},
        ],
    }
    return mount_router(monkeypatch, mod, "/api/leaves", store)


@pytest.fixture
def ts_client(monkeypatch):
    import app.routers.timesheets as mod
    store = {
        "employees": employees_store(),
        "timesheets": [
            {"id": 1, "employee_id": 1, "date": "2026-10-01", "status": "submitted",
             "regular_hours": 8.0, "overtime_hours": 0.0, "holiday_overtime_hours": 0.0,
             "nightshift_hours": 0.0, "total_hours": 8.0},
            {"id": 2, "employee_id": 2, "date": "2026-10-01", "status": "submitted",
             "regular_hours": 8.0, "overtime_hours": 2.0, "holiday_overtime_hours": 0.0,
             "nightshift_hours": 0.0, "total_hours": 10.0},
        ],
    }
    return mount_router(monkeypatch, mod, "/api/timesheets", store)


def test_leave_stats_scoped_counts_own_people(leaves_client):
    leaves_client.as_user(SCOPED)
    assert leaves_client.get("/api/leaves/stats/summary").json()["total"] == 1
    leaves_client.as_user(UNSCOPED)
    assert leaves_client.get("/api/leaves/stats/summary").json()["total"] == 2


def test_timesheet_stats_scoped_counts_own_people(ts_client):
    ts_client.as_user(SCOPED)
    body = ts_client.get("/api/timesheets/stats/summary").json()
    assert body["total_entries"] == 1
    assert body["total_hours"]["total"] == 8.0
    ts_client.as_user(UNSCOPED)
    body = ts_client.get("/api/timesheets/stats/summary").json()
    assert body["total_entries"] == 2
    assert body["total_hours"]["total"] == 18.0
