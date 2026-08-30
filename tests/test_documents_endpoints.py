# tests/test_documents_endpoints.py — covers documents.py's handlers that
# test_documents_folder_rename.py doesn't: list_documents, search_documents,
# list_folders, delete_folder, upload_document (incl. Supabase Storage calls),
# update_document, delete_document. Same "call the route coroutine directly
# against a fake supabase client" recipe, extended with a fake Storage bucket
# for upload_document/delete_document.

import pytest
from fastapi import HTTPException

import app.routers.documents as documents_mod
from app.routers.documents import (
    list_documents, search_documents, list_folders, delete_folder,
    upload_document, update_document, delete_document, DocUpdate, BUCKET,
    create_folder, rename_folder, FolderCreate, FolderUpdate,
)


# ─── Fake supabase (table + storage) ──────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state, response_map):
        self.table_name = table_name
        self.state = state
        self.response_map = response_map
        self._filters = []
        self._or = None
        self._payload = None
        self._op = "select"
        self._single = False

    def select(self, *a, **k): return self
    def eq(self, col, val):
        self._filters.append((col, val))
        return self
    def is_(self, col, val):
        self._filters.append((col, val))
        return self
    def or_(self, expr):
        self._or = expr
        return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def maybe_single(self):
        self._single = True
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters),
             "or": self._or, "payload": self._payload}
        )
        cfg = self.response_map.get(self.table_name, {})
        if callable(cfg):
            return _Resp(cfg(self._op, self._filters, self._payload))
        if self._op == "update":
            return _Resp(cfg.get("update_return", []))
        if self._op == "insert":
            return _Resp(cfg.get("insert_return", []))
        if self._op == "delete":
            return _Resp(cfg.get("delete_return", []))
        if self._single:
            return _Resp(cfg.get("single_return"))
        return _Resp(cfg.get("select_return", []))


class _FakeStorageBucket:
    def __init__(self, state):
        self.state = state

    def upload(self, path, content, opts):
        if self.state.get("upload_raises"):
            raise Exception(self.state["upload_raises"])
        self.state.setdefault("uploaded", []).append((path, content, opts))

    def get_public_url(self, path):
        if self.state.get("public_url_raises"):
            raise Exception("url generation failed")
        return f"https://cdn.example.com/{path}"

    def remove(self, paths):
        if self.state.get("remove_raises"):
            raise Exception("remove failed")
        self.state.setdefault("removed", []).append(paths)


class _FakeStorage:
    def __init__(self, state):
        self.state = state

    def from_(self, bucket):
        self.state["storage_bucket"] = bucket
        return _FakeStorageBucket(self.state)


class _FakeSupabase:
    def __init__(self, state, response_map):
        self.state = state
        self.response_map = response_map
        self.storage = _FakeStorage(state)

    def table(self, name):
        return _FakeQuery(name, self.state, self.response_map)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(response_map: dict, **extra_state):
        state = {"calls": [], **extra_state}
        monkeypatch.setattr(documents_mod, "supabase", _FakeSupabase(state, response_map))
        return state
    return _patch


class _FakeUploadFile:
    def __init__(self, filename, content, content_type="application/pdf"):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self):
        return self._content


# ─── list_documents ────────────────────────────────────────────────────────

async def test_list_documents_root_uses_is_null_filter(patch_supabase):
    state = patch_supabase({"documents": {"select_return": [{"id": "d1"}]}})
    result = await list_documents(category_id="cat1", folder_id=None)
    assert result == [{"id": "d1"}]
    call = state["calls"][0]
    assert ("category_id", "cat1") in call["filters"]
    assert ("folder_id", "null") in call["filters"]


async def test_list_documents_with_folder_filters_by_folder_id(patch_supabase):
    state = patch_supabase({"documents": {"select_return": [{"id": "d2"}]}})
    await list_documents(category_id="cat1", folder_id="Reports")
    call = state["calls"][0]
    assert ("folder_id", "Reports") in call["filters"]


async def test_list_documents_raises_500_on_error(patch_supabase):
    def raiser(op, filters, payload):
        raise RuntimeError("db down")
    patch_supabase({"documents": raiser})
    with pytest.raises(HTTPException) as exc:
        await list_documents(category_id="cat1", folder_id=None)
    assert exc.value.status_code == 500


# ─── search_documents ──────────────────────────────────────────────────────

async def test_search_documents_blank_query_short_circuits(patch_supabase):
    state = patch_supabase({})
    result = await search_documents(q="   ")
    assert result == []
    assert state["calls"] == []


async def test_search_documents_returns_matches(patch_supabase):
    state = patch_supabase({"documents": {"select_return": [{"id": "d1", "name": "Report"}]}})
    result = await search_documents(q="report")
    assert result == [{"id": "d1", "name": "Report"}]
    call = state["calls"][0]
    assert "report" in call["or"]


async def test_search_documents_raises_500_on_error(patch_supabase):
    def raiser(op, filters, payload):
        raise RuntimeError("boom")
    patch_supabase({"documents": raiser})
    with pytest.raises(HTTPException) as exc:
        await search_documents(q="x")
    assert exc.value.status_code == 500


# ─── list_folders ───────────────────────────────────────────────────────────

async def test_list_folders_returns_rows_for_category(patch_supabase):
    state = patch_supabase({"document_folders": {"select_return": [{"id": "f1", "name": "A"}]}})
    result = await list_folders(category_id="cat1")
    assert result == [{"id": "f1", "name": "A"}]
    assert ("category_id", "cat1") in state["calls"][0]["filters"]


async def test_list_folders_raises_500_on_error(patch_supabase):
    def raiser(op, filters, payload):
        raise RuntimeError("boom")
    patch_supabase({"document_folders": raiser})
    with pytest.raises(HTTPException) as exc:
        await list_folders(category_id="cat1")
    assert exc.value.status_code == 500


# ─── delete_folder ──────────────────────────────────────────────────────────

async def test_delete_folder_success(patch_supabase):
    state = patch_supabase({"document_folders": {"delete_return": [{"id": "f1"}]}})
    result = await delete_folder("f1", current_user={"user_id": "u1", "role": "manager"})
    assert result == {"ok": True}
    assert state["calls"][0]["op"] == "delete"
    assert ("id", "f1") in state["calls"][0]["filters"]


async def test_delete_folder_raises_500_on_error(patch_supabase):
    def raiser(op, filters, payload):
        raise RuntimeError("boom")
    patch_supabase({"document_folders": raiser})
    with pytest.raises(HTTPException) as exc:
        await delete_folder("f1", current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 500


# ─── create_folder / rename_folder — error branches not covered by
#     test_documents_folder_rename.py (which owns the cascade/duplicate-name
#     behavior tests) ───────────────────────────────────────────────────────

async def test_create_folder_blank_name_is_rejected_before_any_query(patch_supabase):
    state = patch_supabase({})
    with pytest.raises(HTTPException) as exc:
        await create_folder(FolderCreate(category_id="cat1", name="   "), current_user={"user_id": "u1"})
    assert exc.value.status_code == 400
    assert state["calls"] == []


async def test_create_folder_raises_500_on_error(patch_supabase):
    def raiser(op, filters, payload):
        raise RuntimeError("boom")
    patch_supabase({"document_folders": raiser})
    with pytest.raises(HTTPException) as exc:
        await create_folder(FolderCreate(category_id="cat1", name="Reports"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


async def test_rename_folder_raises_500_on_error(patch_supabase):
    def raiser(op, filters, payload):
        raise RuntimeError("boom")
    patch_supabase({"document_folders": raiser})
    with pytest.raises(HTTPException) as exc:
        await rename_folder("f1", FolderUpdate(name="New Name"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── upload_document ─────────────────────────────────────────────────────────

async def test_upload_document_happy_path_inserts_row(patch_supabase):
    state = patch_supabase({"documents": {"insert_return": [{"id": "doc1", "name": "report.pdf"}]}})
    result = await upload_document(
        file=_FakeUploadFile("report.pdf", b"hello world", content_type="application/pdf"),
        name="", description="  a note  ", category_id="cat1", category_name="Cat One",
        folder_id="", folder_path="", current_user={"user_id": "u1"},
    )
    assert result == {"id": "doc1", "name": "report.pdf"}

    insert_call = next(c for c in state["calls"] if c["table"] == "documents")
    payload = insert_call["payload"]
    assert payload["name"] == "report.pdf"          # falls back to original filename
    assert payload["original_name"] == "report.pdf"
    assert payload["file_type"] == "pdf"
    assert payload["file_size"] == len(b"hello world")
    assert payload["mime_type"] == "application/pdf"
    assert payload["description"] == "a note"        # stripped
    assert payload["folder_id"] is None               # blank folder_id -> root
    assert payload["storage_path"].startswith("cat1/root/")
    assert payload["file_url"].startswith("https://cdn.example.com/")
    assert state["uploaded"][0][0] == payload["storage_path"]


async def test_upload_document_uses_explicit_name_over_filename(patch_supabase):
    state = patch_supabase({"documents": {"insert_return": [{"id": "doc1"}]}})
    await upload_document(
        file=_FakeUploadFile("raw_export_2024.pdf", b"x"),
        name="Q1 Report", description="", category_id="cat1", category_name="",
        folder_id="Reports", folder_path="Reports", current_user={"user_id": "u1"},
    )
    payload = state["calls"][-1]["payload"]
    assert payload["name"] == "Q1 Report"
    assert payload["folder_id"] == "Reports"
    assert payload["storage_path"].startswith("cat1/Reports/")


async def test_upload_document_rejects_disallowed_extension_before_storage_call(patch_supabase):
    state = patch_supabase({})
    with pytest.raises(HTTPException) as exc:
        await upload_document(
            file=_FakeUploadFile("virus.exe", b"x"),
            name="", description="", category_id="cat1", category_name="",
            folder_id="", folder_path="", current_user={"user_id": "u1"},
        )
    assert exc.value.status_code == 400
    assert "uploaded" not in state  # never reached the storage layer


async def test_upload_document_storage_upload_failure_raises_500(patch_supabase):
    patch_supabase({}, upload_raises="disk full")
    with pytest.raises(HTTPException) as exc:
        await upload_document(
            file=_FakeUploadFile("report.pdf", b"x"),
            name="", description="", category_id="cat1", category_name="",
            folder_id="", folder_path="", current_user={"user_id": "u1"},
        )
    assert exc.value.status_code == 500
    assert "Storage upload failed" in exc.value.detail


async def test_upload_document_public_url_failure_still_inserts_with_empty_url(patch_supabase):
    state = patch_supabase({"documents": {"insert_return": [{"id": "doc1"}]}}, public_url_raises=True)
    await upload_document(
        file=_FakeUploadFile("report.pdf", b"x"),
        name="", description="", category_id="cat1", category_name="",
        folder_id="", folder_path="", current_user={"user_id": "u1"},
    )
    payload = state["calls"][-1]["payload"]
    assert payload["file_url"] == ""


async def test_upload_document_insert_failure_raises_500(patch_supabase):
    def raiser(op, filters, payload):
        raise RuntimeError("insert failed")
    patch_supabase({"documents": raiser})
    with pytest.raises(HTTPException) as exc:
        await upload_document(
            file=_FakeUploadFile("report.pdf", b"x"),
            name="", description="", category_id="cat1", category_name="",
            folder_id="", folder_path="", current_user={"user_id": "u1"},
        )
    assert exc.value.status_code == 500


# ─── update_document ─────────────────────────────────────────────────────────

async def test_update_document_only_sends_provided_fields(patch_supabase):
    state = patch_supabase({"documents": {"update_return": [{"id": "d1", "starred": True}]}})
    result = await update_document("d1", DocUpdate(starred=True), current_user={"user_id": "u1"})
    assert result == {"id": "d1", "starred": True}
    payload = state["calls"][0]["payload"]
    assert payload["starred"] is True
    assert "name" not in payload
    assert "description" not in payload
    assert "updated_at" in payload


async def test_update_document_404_when_missing(patch_supabase):
    patch_supabase({"documents": {"update_return": []}})
    with pytest.raises(HTTPException) as exc:
        await update_document("ghost", DocUpdate(name="x"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 404


async def test_update_document_raises_500_on_error(patch_supabase):
    def raiser(op, filters, payload):
        raise RuntimeError("boom")
    patch_supabase({"documents": raiser})
    with pytest.raises(HTTPException) as exc:
        await update_document("d1", DocUpdate(name="x"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 500


# ─── delete_document ──────────────────────────────────────────────────────────

async def test_delete_document_removes_storage_object_and_row(patch_supabase):
    state = patch_supabase({
        "documents": {"single_return": {"storage_path": "cat1/root/abc.pdf"}, "delete_return": [{"id": "d1"}]},
    })
    result = await delete_document("d1", current_user={"user_id": "u1", "role": "manager"})
    assert result == {"ok": True}
    assert state["removed"] == [["cat1/root/abc.pdf"]]
    delete_call = next(c for c in state["calls"] if c["op"] == "delete")
    assert ("id", "d1") in delete_call["filters"]


async def test_delete_document_skips_storage_removal_when_no_storage_path(patch_supabase):
    state = patch_supabase({
        "documents": {"single_return": {"storage_path": None}, "delete_return": [{"id": "d1"}]},
    })
    result = await delete_document("d1", current_user={"user_id": "u1", "role": "manager"})
    assert result == {"ok": True}
    assert "removed" not in state


async def test_delete_document_continues_when_storage_removal_fails(patch_supabase):
    state = patch_supabase({
        "documents": {"single_return": {"storage_path": "cat1/root/abc.pdf"}, "delete_return": [{"id": "d1"}]},
    }, remove_raises=True)
    result = await delete_document("d1", current_user={"user_id": "u1", "role": "manager"})
    # Storage failure is swallowed (logged only) — the DB row is still deleted.
    assert result == {"ok": True}


async def test_delete_document_raises_500_when_select_errors(patch_supabase):
    def raiser(op, filters, payload):
        raise RuntimeError("boom")
    patch_supabase({"documents": raiser})
    with pytest.raises(HTTPException) as exc:
        await delete_document("d1", current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 500
