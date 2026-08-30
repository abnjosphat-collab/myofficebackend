# tests/test_documents_folder_rename.py — documents.py's folders are tagged onto a
# document by free-text name (folder_id/folder_path aren't a real foreign key — see
# upload_document's own comment), so rename_folder has to cascade the name change onto
# every document currently tagged with the old name, or they'd silently vanish from the
# folder's file list. That cascade, create_folder's duplicate-name guard, and _file_type
# (the extension classifier used at upload time) had zero tests; documents.py was 26%
# covered. Uses the sanctioned "call the route coroutine directly against a fake
# supabase client" recipe, generalized to track every call made (table/op/filters/
# payload) so the cascade's exact filter and payload can be asserted on directly.

import pytest

import app.routers.documents as documents_mod
from app.routers.documents import _file_type, FolderCreate, FolderUpdate, create_folder, rename_folder


# ─── _file_type ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("filename,expected", [
    ("photo.jpg", "image"), ("photo.PNG", "image"),
    ("clip.mp4", "video"),
    ("song.mp3", "audio"),
    ("report.docx", "document"),
    ("data.xlsx", "spreadsheet"), ("data.csv", "spreadsheet"),
    ("manual.pdf", "pdf"),
    ("archive.zip", "archive"),
])
def test_file_type_classifies_known_extensions(filename, expected):
    assert _file_type(filename) == expected


def test_file_type_unknown_extension_is_file():
    assert _file_type("data.xyz123") == "file"


def test_file_type_no_extension_is_file():
    assert _file_type("README") == "file"


# ─── Fake supabase — records every call for assertion, not just fixed responses ───

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state, response_map):
        self.table_name = table_name
        self.state = state
        self._response_map = response_map
        self._filters = []
        self._payload = None
        self._op = "select"

    def select(self, *a, **k): return self
    def eq(self, col, val):
        self._filters.append((col, val))
        return self
    def maybe_single(self): return self
    def order(self, *a, **k): return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def execute(self):
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        table_cfg = self._response_map.get(self.table_name, {})
        if self._op == "update":
            return _Resp(table_cfg.get("update_return", [{**(self._payload or {})}]))
        if self._op == "insert":
            return _Resp(table_cfg.get("insert_return", [{"id": "new", **(self._payload or {})}]))
        return _Resp(table_cfg.get("select_return"))


class _FakeSupabase:
    def __init__(self, state, response_map):
        self.state = state
        self.response_map = response_map

    def table(self, name):
        return _FakeQuery(name, self.state, self.response_map)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(response_map: dict):
        state = {"calls": []}
        monkeypatch.setattr(documents_mod, "supabase", _FakeSupabase(state, response_map))
        return state
    return _patch


# ─── rename_folder — the cascade onto tagged documents ──────────────────────────────

async def test_rename_cascades_onto_documents_tagged_with_old_name(patch_supabase):
    state = patch_supabase({
        "document_folders": {
            "select_return": {"id": "f1", "name": "Old Name", "category_id": "cat1"},
            "update_return": [{"id": "f1", "name": "New Name", "category_id": "cat1"}],
        },
    })
    result = await rename_folder("f1", FolderUpdate(name="New Name"), current_user={"user_id": "u1"})
    assert result["name"] == "New Name"

    doc_updates = [c for c in state["calls"] if c["table"] == "documents" and c["op"] == "update"]
    assert len(doc_updates) == 1
    assert doc_updates[0]["payload"] == {"folder_id": "New Name", "folder_path": "New Name"}
    assert ("category_id", "cat1") in doc_updates[0]["filters"]
    assert ("folder_id", "Old Name") in doc_updates[0]["filters"]


async def test_rename_to_the_same_name_does_not_cascade(patch_supabase):
    # Renaming "Old Name" -> "Old Name" (e.g. a no-op save) must not fire the
    # documents cascade update at all.
    state = patch_supabase({
        "document_folders": {
            "select_return": {"id": "f1", "name": "Old Name", "category_id": "cat1"},
            "update_return": [{"id": "f1", "name": "Old Name", "category_id": "cat1"}],
        },
    })
    await rename_folder("f1", FolderUpdate(name="Old Name"), current_user={"user_id": "u1"})
    doc_updates = [c for c in state["calls"] if c["table"] == "documents"]
    assert doc_updates == []


async def test_rename_nonexistent_folder_is_404(patch_supabase):
    patch_supabase({"document_folders": {"select_return": None}})
    with pytest.raises(Exception) as exc_info:
        await rename_folder("missing", FolderUpdate(name="X"), current_user={"user_id": "u1"})
    assert "404" in str(exc_info.value) or getattr(exc_info.value, "status_code", None) == 404


async def test_rename_blank_name_is_rejected_before_any_query(patch_supabase):
    state = patch_supabase({})
    with pytest.raises(Exception):
        await rename_folder("f1", FolderUpdate(name="   "), current_user={"user_id": "u1"})
    assert state["calls"] == []


# ─── create_folder — duplicate-name guard ───────────────────────────────────────────

async def test_create_folder_rejects_duplicate_name_in_same_category(patch_supabase):
    patch_supabase({"document_folders": {"select_return": {"id": "existing"}}})
    with pytest.raises(Exception) as exc_info:
        await create_folder(FolderCreate(category_id="cat1", name="Reports"), current_user={"user_id": "u1"})
    assert getattr(exc_info.value, "status_code", None) == 409


async def test_create_folder_succeeds_when_name_is_unique(patch_supabase):
    patch_supabase({
        "document_folders": {
            "select_return": None,
            "insert_return": [{"id": "f2", "category_id": "cat1", "name": "Reports"}],
        },
    })
    result = await create_folder(FolderCreate(category_id="cat1", name="Reports"), current_user={"user_id": "u1"})
    assert result["name"] == "Reports"
