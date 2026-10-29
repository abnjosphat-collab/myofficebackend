# app/routers/documents.py — the document library's files: one overview, one search that also serves every folder view,
# upload, edit and move, bulk actions, trash, history. Folders (the tree and the AMS structure) are in document_folders.py.
#
# The rules that make a file hard to lose:
#   * a file belongs to a real folder by id (folder_ref), never by a typed name;
#   * "delete" moves a file to the trash; only a manager removes it for good, and only from the trash;
#   * the same file (identical content) cannot be put in one folder twice, and two files with one name get "(2)";
#   * who uploaded and who deleted come from the sign-in, never from the browser;
#   * every change is written to the document history.
#
# A print copy: a PDF of a Word or Excel file, kept on the file's own row (print_copy_*), so Print sends the exact pages the
# author laid out instead of the browser's redrawing of them. It goes with the file: replaced in place, removed with it.
import logging
import re
import uuid as uuid_module
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from postgrest.types import CountMethod
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user, require_role, role_at_least
from app.concurrency import assert_unchanged, touch
from app.db_helpers import fetch_all_pages, response_rows
from app.document_rules import (
    HUB_UPLOAD_EXTS, MAX_UPLOAD_BYTES, clean_display_name, content_hash, content_type_for, descendant_ids,
    ext_of, extract_text, file_type_of, folder_path, normalise_tags, search_terms, sort_spec, unique_display_name,
)
from app.document_structure import plan_seed, summarise
from app.documents_support import (
    LIST_COLUMNS, apply_filters, found_inside, get_folder, is_uuid, load_folders, log_activity, relevance, require_upgrade, shown,
)
from app.errors import server_error
from app.scope import assert_row_visible, scope_subtree_ids, viewer_scope
from app.supabase_client import supabase
from app.uploads import read_and_validate_upload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

BUCKET = "ams-documents"
MAX_SEARCH_ROWS = 500
MAX_BULK = 200
# Reading the words out of a very large file costs more memory than the search is worth on this server: it is still saved
# and found by its name and details.
MAX_EXTRACT_BYTES = 25 * 1024 * 1024
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ZERO = "00000000-0000-0000-0000-000000000000"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _in_folder(query, folder_ref: Optional[str]):
    return query.is_("folder_ref", "null") if folder_ref is None else query.eq("folder_ref", folder_ref)


def _names_in(folder_ref: Optional[str]) -> List[str]:
    rows = response_rows(_in_folder(supabase.table("documents").select("name").is_("deleted_at", "null"), folder_ref).execute())
    return [str(r["name"]) for r in rows]


def _copy_in(folder_ref: Optional[str], digest: Optional[str], ignore_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """A live file with identical content already in this folder, or None."""
    if not digest:
        return None
    query = _in_folder(supabase.table("documents").select("id,name").eq("content_hash", digest).is_("deleted_at", "null"), folder_ref)
    for row in response_rows(query.execute()):
        if str(row["id"]) != str(ignore_id):
            return row
    return None


def _get_doc(doc_id: str, columns: str = LIST_COLUMNS + ",content_hash,storage_path,print_copy_path") -> Dict[str, Any]:
    if not is_uuid(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    found = response_rows(supabase.table("documents").select(columns).eq("id", doc_id).execute())
    if not found:
        raise HTTPException(status_code=404, detail="Document not found")
    return found[0]


def _check_date(value: Optional[str], label: str) -> Optional[str]:
    if value and not _DATE.match(value):
        raise HTTPException(status_code=422, detail=f"{label} must be a date like 2026-10-19.")
    return value or None


# ── Overview: the numbers behind the folder tree and the filters ──────────────────────────────────────────────────────

@router.get("/overview")
async def overview(current_user: dict = Depends(get_current_user)):
    require_upgrade(supabase)
    try:
        def read():
            folders = load_folders(supabase)
            docs = fetch_all_pages(lambda s, e: supabase.table("documents").select("folder_ref,deleted_at,file_type,ext,tags,uploaded_by,starred,text_indexed_at").order("id").range(s, e).execute())
            return folders, docs
        folders, docs = await run_in_threadpool(read)
    except Exception as e:
        raise server_error(e, "Loading the document library")

    scope = viewer_scope(current_user)
    if scope is not None:
        ids = set(scope_subtree_ids(folders, scope))
        folders = [f for f in folders if str(f["id"]) in ids]
        docs = [d for d in docs if d.get("folder_ref") and str(d["folder_ref"]) in ids]

    live = [d for d in docs if not d.get("deleted_at")]
    direct = Counter(str(d["folder_ref"]) for d in live if d.get("folder_ref"))
    total = {str(f["id"]): sum(direct[i] for i in descendant_ids(folders, str(f["id"]))) for f in folders}
    tags = Counter(t for d in live for t in (d.get("tags") or []))
    people = Counter(d["uploaded_by"] for d in live if d.get("uploaded_by"))
    return {
        "folders": [{**f, "file_count": direct[str(f["id"])], "total_count": total[str(f["id"])]} for f in folders],
        "counts": {
            "all": len(live), "unfiled": sum(1 for d in live if not d.get("folder_ref")),
            "trashed": len(docs) - len(live), "starred": sum(1 for d in live if d.get("starred")),
            "unindexed": sum(1 for d in live if not d.get("text_indexed_at")),
        },
        "facets": {
            "types": dict(Counter(d["file_type"] for d in live)),
            "exts": [{"ext": k, "count": v} for k, v in Counter(d["ext"] for d in live if d.get("ext")).most_common()],
            "tags": [{"tag": k, "count": v} for k, v in tags.most_common()],
            "uploaders": [{"name": k, "count": v} for k, v in people.most_common()],
        },
        # A scoped viewer can neither seed nor reindex (both are whole-library
        # jobs, refused with 403): their slice reports itself complete so no
        # setup banner invites them to press a button that cannot work.
        "seeded": True if scope is not None else any(f.get("seed_key") == "ams" for f in folders),
        # How many folders of the AMS structure do not exist yet (0 once "Set up the AMS folders" has been pressed).
        "ams_missing": 0 if scope is not None else summarise(plan_seed(folders))["created"],
    }


# ── Search: every folder view and the universal search are this one query ────────────────────────────────────────────

@router.get("/search")
async def search_documents(
    q: str = "", folder_id: Optional[str] = None, recursive: bool = True, unfiled: bool = False, trashed: bool = False,
    types: Optional[str] = None, exts: Optional[str] = None, starred: bool = False,
    added_from: Optional[str] = None, added_to: Optional[str] = None, min_size: Optional[int] = Query(None, ge=0),
    max_size: Optional[int] = Query(None, ge=0), tags: Optional[str] = None, uploader: Optional[str] = None,
    names_only: bool = False, sort: Optional[str] = None, dir: Optional[str] = None,
    limit: int = Query(300, ge=1, le=MAX_SEARCH_ROWS), current_user: dict = Depends(get_current_user),
):
    require_upgrade(supabase)
    terms = search_terms(q)
    added_from, added_to = _check_date(added_from, "From date"), _check_date(added_to, "To date")
    scope = viewer_scope(current_user)
    sub_ids: Optional[List[str]] = None
    if scope is not None:
        if unfiled:
            # Unfiled files sit outside every subtree: a scoped search of
            # them is honestly empty.
            return {"items": [], "total": 0, "limit": limit, "truncated": False, "terms": terms}
        sub_ids = scope_subtree_ids(await run_in_threadpool(lambda: load_folders(supabase)), scope)
    try:
        folder_ids: Optional[List[str]] = None
        if folder_id and not unfiled:
            if recursive:
                folder_ids = descendant_ids(await run_in_threadpool(lambda: load_folders(supabase)), folder_id)
            else:
                folder_ids = [folder_id]
            if not all(is_uuid(i) for i in folder_ids):
                raise HTTPException(status_code=422, detail="Choose a folder from the list.")
        if sub_ids is not None:
            if folder_ids is None:
                folder_ids = sub_ids
            else:
                if folder_id not in sub_ids:
                    raise HTTPException(status_code=404, detail="That folder no longer exists. Reload the page.")
                folder_ids = [i for i in folder_ids if i in sub_ids]

        by_relevance = sort == "relevance" or (sort is None and bool(terms))
        column, descending = sort_spec(None if by_relevance else sort, dir)

        def run():
            query = supabase.table("documents").select(LIST_COLUMNS, count=CountMethod.exact)
            query = apply_filters(
                query, terms=terms, folder_ids=folder_ids, unfiled=unfiled, trashed=trashed, types=types, exts=exts,
                starred=starred, added_from=added_from, added_to=added_to, min_size=min_size, max_size=max_size, tags=tags,
                uploader=uploader, names_only=names_only)
            return query.order(column, desc=descending).order("id", desc=True).limit(limit).execute()

        result = await run_in_threadpool(run)
    except Exception as e:
        raise server_error(e, "Searching documents")

    rows = response_rows(result)
    if by_relevance and terms:
        rows.sort(key=lambda r: -relevance(r, terms))  # stable: newest first among equals
    total = result.count if isinstance(result.count, int) else len(rows)
    return {
        "items": [{**shown(r), "found_inside": found_inside(r, terms)} for r in rows],
        "total": total, "limit": limit, "truncated": total > len(rows), "terms": terms,
    }


# ── Upload ───────────────────────────────────────────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...), name: str = Form(""), description: str = Form(""), folder_id: str = Form(""),
    tags: str = Form(""), current_user: dict = Depends(get_current_user),
):
    require_upgrade(supabase)
    scope = viewer_scope(current_user)
    folders = await run_in_threadpool(lambda: load_folders(supabase))
    folder = get_folder(supabase, folder_id.strip(), current_user, folders) if folder_id.strip() else None
    if scope is not None and folder is None:
        raise HTTPException(status_code=404, detail=f"Choose a folder inside {scope}.")
    try:
        wanted_tags = normalise_tags(t for t in tags.split(","))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    content = await read_and_validate_upload(file, max_bytes=MAX_UPLOAD_BYTES, allowed_exts=HUB_UPLOAD_EXTS)
    if not content:
        raise HTTPException(status_code=400, detail="That file is empty.")

    # a file name can arrive with the sender's folder path in it ("C:\Users\me\x.pdf"): keep only the name
    original_name = re.split(r"[\\/]", file.filename or "upload")[-1] or "upload"
    ext = ext_of(original_name)
    folder_ref = str(folder["id"]) if folder else None
    digest = content_hash(content)

    same = _copy_in(folder_ref, digest)
    if same:
        raise HTTPException(status_code=409, detail=f"This exact file is already in this folder as “{same['name']}”.")
    display_name = unique_display_name(clean_display_name(name, original_name), _names_in(folder_ref))
    storage_path = f"{folder_ref or 'unfiled'}/{uuid_module.uuid4()}.{ext}"

    try:
        supabase.storage.from_(BUCKET).upload(storage_path, content, {"content-type": content_type_for(original_name)})
    except Exception as e:
        raise server_error(e, "Storing the file")
    try:
        file_url = supabase.storage.from_(BUCKET).get_public_url(storage_path)
    except Exception as e:
        # The file is stored; only its address failed. Saved without one, the page says "no link" instead of a dead link.
        logger.error("get_public_url failed for %s: %s", storage_path, e)
        file_url = ""

    text = await run_in_threadpool(extract_text, content, ext) if len(content) <= MAX_EXTRACT_BYTES else ""
    now = _now()
    row = {
        "name": display_name, "original_name": original_name, "storage_path": storage_path, "file_url": file_url,
        "file_size": len(content), "mime_type": content_type_for(original_name), "file_type": file_type_of(original_name), "ext": ext,
        "folder_ref": folder_ref, "folder_path": folder_path(folders, folder_ref), "description": description.strip(), "tags": wanted_tags,
        "starred": False, "content_hash": digest, "uploaded_by": current_user.get("name") or current_user.get("email") or "",
        "content_text": text, "text_indexed_at": now, "created_at": now, "updated_at": now,
    }
    try:
        saved = response_rows(supabase.table("documents").insert(row).execute())[0]
    except Exception as e:
        try:  # the file would otherwise sit in storage with nothing pointing at it
            supabase.storage.from_(BUCKET).remove([storage_path])
        except Exception as cleanup:
            logger.error("Could not remove the orphaned upload %s: %s", storage_path, cleanup)
        raise server_error(e, "Saving the document")

    log_activity(supabase, current_user, "upload", saved["id"], name=display_name, folder=row["folder_path"], size=len(content))
    elsewhere = [r for r in response_rows(supabase.table("documents").select("id,name,folder_path,folder_ref").eq("content_hash", digest).is_("deleted_at", "null").limit(4).execute()) if r["id"] != saved["id"]]
    if scope is not None:
        # "Also in" must not name files outside the viewer's subtree; the
        # limit(4) above can only under-report, never leak.
        ids = set(scope_subtree_ids(folders, scope))
        elsewhere = [r for r in elsewhere if r.get("folder_ref") and str(r["folder_ref"]) in ids]
    return {**shown(saved), "also_in": [{"id": r["id"], "name": r["name"], "folder_path": r["folder_path"]} for r in elsewhere[:3]]}


# ── Edit and move ────────────────────────────────────────────────────────────────────────────────────────────────────

class DocUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    starred: Optional[bool] = None
    tags: Optional[List[str]] = None
    folder_id: Optional[str] = None  # sent as null it means "take it out of its folder" (Unfiled); not sent, it stays
    expected_updated_at: Optional[str] = None


def _move_changes(doc: Dict[str, Any], folders: List[Dict[str, Any]], target_id: Optional[str], user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The changes that put `doc` in another folder: its path, and a number on its name if the folder already has one."""
    if target_id is not None:
        get_folder(supabase, target_id, user, folders)
    elif viewer_scope(user) is not None:
        raise HTTPException(status_code=404, detail=f"Choose a folder inside {viewer_scope(user)}.")
    if (str(doc.get("folder_ref")) if doc.get("folder_ref") else None) == target_id:
        return {}
    same = _copy_in(target_id, doc.get("content_hash"), doc["id"])
    if same:
        raise HTTPException(status_code=409, detail=f"This exact file is already in that folder as “{same['name']}”.")
    return {
        "folder_ref": target_id, "folder_path": folder_path(folders, target_id),
        "name": unique_display_name(doc["name"], _names_in(target_id)),
    }


@router.put("/{doc_id}")
async def update_document(doc_id: str, body: DocUpdate, current_user: dict = Depends(get_current_user)):
    require_upgrade(supabase)
    try:
        doc = _get_doc(doc_id)
        if viewer_scope(current_user) is not None:
            assert_row_visible(current_user, "documents", doc, supabase, detail="Document not found")
        assert_unchanged(doc, body.expected_updated_at)
        changes: Dict[str, Any] = {}
        if body.name is not None:
            new_name = clean_display_name(body.name, doc["name"])
            if new_name != doc["name"] and new_name.lower() in {n.lower() for n in _names_in(doc.get("folder_ref"))}:
                raise HTTPException(status_code=409, detail="A file with this name is already in this folder.")
            changes["name"] = new_name
        if body.description is not None:
            changes["description"] = body.description.strip()
        if body.starred is not None:
            changes["starred"] = body.starred
        if body.tags is not None:
            try:
                changes["tags"] = normalise_tags(body.tags)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
        if "folder_id" in body.model_fields_set:
            folders = await run_in_threadpool(lambda: load_folders(supabase))
            changes.update(_move_changes({**doc, **changes}, folders, body.folder_id or None, current_user))
        if not changes:
            return shown(doc)
        touch(doc, changes)
        saved = response_rows(supabase.table("documents").update(changes).eq("id", doc_id).execute())
        if not saved:
            raise HTTPException(status_code=404, detail="Document not found")
        what = [k for k in changes if k not in ("updated_at", "folder_path")]
        if set(what) != {"starred"}:  # starring is too small to be history
            log_activity(supabase, current_user, "move" if "folder_ref" in changes else "update", doc_id,
                         name=changes.get("name", doc["name"]), changed=what, to=changes.get("folder_path"))
        return shown(saved[0])
    except Exception as e:
        raise server_error(e, "Updating the document")


# ── Bulk actions ─────────────────────────────────────────────────────────────────────────────────────────────────────

class BulkAction(BaseModel):
    ids: List[str]
    action: str
    folder_id: Optional[str] = None
    tags: Optional[List[str]] = None


BULK_ACTIONS = {"star", "unstar", "move", "tag_add", "tag_remove", "trash", "restore", "purge"}
_MANAGER_ONLY = {"trash", "purge"}  # deleting stays with managers, as it always has


@router.post("/bulk")
async def bulk_action(body: BulkAction, current_user: dict = Depends(get_current_user)):
    """Apply one action to many files. A file that cannot take it is reported by name; the others still go through."""
    require_upgrade(supabase)
    if body.action not in BULK_ACTIONS:
        raise HTTPException(status_code=422, detail="Unknown action.")
    ids = list(dict.fromkeys(i for i in body.ids if is_uuid(i)))
    if not ids:
        raise HTTPException(status_code=422, detail="Choose at least one file.")
    if len(ids) > MAX_BULK:
        raise HTTPException(status_code=422, detail=f"Choose at most {MAX_BULK} files at a time.")
    if body.action in _MANAGER_ONLY:
        _need_manager(current_user)
    try:
        rows = {r["id"]: r for r in response_rows(supabase.table("documents").select(LIST_COLUMNS + ",content_hash,storage_path,print_copy_path").in_("id", ids).execute())}
        done: List[str] = []
        failed: List[Dict[str, str]] = []
        scope = viewer_scope(current_user)
        folders = await run_in_threadpool(lambda: load_folders(supabase)) if body.action == "move" or scope is not None else []
        wanted_tags = normalise_tags(body.tags) if body.action in ("tag_add", "tag_remove") else []
        target = body.folder_id or None
        sub = set(scope_subtree_ids(folders, scope)) if scope is not None else set()
        if scope is not None and body.action == "move" and (target is None or target not in sub):
            raise HTTPException(status_code=404, detail=f"Choose a folder inside {scope}.")

        for doc_id in ids:
            doc = rows.get(doc_id)
            if doc is None:
                failed.append({"id": doc_id, "error": "It no longer exists."})
                continue
            if scope is not None and str(doc.get("folder_ref") or "") not in sub:
                failed.append({"id": doc_id, "error": "It no longer exists."})
                continue
            try:
                _apply_one(body.action, doc, folders, target, wanted_tags, current_user)
                done.append(doc_id)
            except (HTTPException, ValueError) as e:
                failed.append({"id": doc_id, "name": doc["name"], "error": str(e.detail if isinstance(e, HTTPException) else e)})
            except Exception as e:
                ref = server_error(e, f"Applying {body.action}").detail
                failed.append({"id": doc_id, "name": doc["name"], "error": str(ref)})
        return {"done": done, "failed": failed}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise server_error(e, "Applying the action")


def _need_manager(user: Dict[str, Any]) -> None:
    if not role_at_least(user.get("role", ""), "manager"):
        raise HTTPException(status_code=403, detail=f"Permission denied. Requires manager+ role. Your role: {user.get('role', '')}.")


def _apply_one(action: str, doc: Dict[str, Any], folders: List[Dict[str, Any]], target: Optional[str], tags: List[str], user: Dict[str, Any]) -> None:
    doc_id = doc["id"]
    if action in ("star", "unstar"):
        changes: Dict[str, Any] = {"starred": action == "star"}
    elif action == "move":
        changes = _move_changes(doc, folders, target, user)
    elif action in ("tag_add", "tag_remove"):
        current = list(doc.get("tags") or [])
        changes = {"tags": normalise_tags([*current, *tags] if action == "tag_add" else [t for t in current if t not in tags])}
    elif action == "trash":
        if doc.get("deleted_at"):
            raise HTTPException(status_code=409, detail="It is already in the trash.")
        changes = {"deleted_at": _now(), "deleted_by": user.get("name") or user.get("email") or ""}
    elif action == "restore":
        if not doc.get("deleted_at"):
            raise HTTPException(status_code=409, detail="It is not in the trash.")
        same = _copy_in(doc.get("folder_ref"), doc.get("content_hash"), doc_id)
        if same:
            raise HTTPException(status_code=409, detail=f"The same file is already in that folder as “{same['name']}”.")
        changes = {"deleted_at": None, "deleted_by": "", "name": unique_display_name(doc["name"], _names_in(doc.get("folder_ref")))}
    else:  # purge
        _purge(doc, user)
        return
    touch(doc, changes)
    supabase.table("documents").update(changes).eq("id", doc_id).execute()
    if action not in ("star", "unstar"):
        log_activity(supabase, user, {"trash": "trash", "restore": "restore", "move": "move"}.get(action, "update"), doc_id, name=doc["name"], to=changes.get("folder_path"))


def _purge(doc: Dict[str, Any], user: Dict[str, Any]) -> None:
    if not doc.get("deleted_at"):
        raise HTTPException(status_code=409, detail="Move it to the trash first. Only a file in the trash can be deleted for good.")
    paths = [p for p in (doc.get("storage_path"), doc.get("print_copy_path")) if p]
    if paths:
        try:
            supabase.storage.from_(BUCKET).remove(paths)
        except Exception as e:
            raise HTTPException(status_code=502, detail="The stored file could not be removed, so nothing was deleted. Try again.") from e
    supabase.table("documents").delete().eq("id", doc["id"]).execute()
    log_activity(supabase, user, "purge", doc["id"], name=doc["name"], folder=doc.get("folder_path"))


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, permanent: bool = False, current_user: dict = Depends(require_role("manager"))):
    """Move a file to the trash; with ?permanent=true remove a file that is already in the trash."""
    require_upgrade(supabase)
    try:
        doc = _get_doc(doc_id)
        if viewer_scope(current_user) is not None:
            assert_row_visible(current_user, "documents", doc, supabase, detail="Document not found")
        if permanent:
            _purge(doc, current_user)
        else:
            _apply_one("trash", doc, [], None, [], current_user)
        return {"ok": True}
    except Exception as e:
        raise server_error(e, "Deleting the document")


# ── Print copy ───────────────────────────────────────────────────────────────────────────────────────────────────────

PRINT_COPY_EXTS = {"pdf"}
NO_PRINT_COPY = {"print_copy_path": None, "print_copy_url": None, "print_copy_name": None, "print_copy_size": None}


def _visible_doc(doc_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    doc = _get_doc(doc_id)
    if viewer_scope(user) is not None:
        assert_row_visible(user, "documents", doc, supabase, detail="Document not found")
    if doc.get("deleted_at"):
        raise HTTPException(status_code=409, detail="It is in the trash. Restore it first.")
    return doc


def _drop_stored(path: Optional[str]) -> None:
    """Remove a replaced or removed print copy from storage. A failure only leaves an unreferenced file, so it is logged."""
    if not path:
        return
    try:
        supabase.storage.from_(BUCKET).remove([path])
    except Exception as e:
        logger.error("Could not remove the old print copy %s: %s", path, e)


@router.post("/{doc_id}/print-copy")
async def set_print_copy(doc_id: str, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """Keep a PDF with this file for printing (Word: File, Save as, PDF). Replaces the one it had."""
    require_upgrade(supabase)
    doc = _visible_doc(doc_id, current_user)
    if (doc.get("ext") or "").lower() == "pdf":
        raise HTTPException(status_code=422, detail="This file is already a PDF: it prints exactly as it is.")
    content = await read_and_validate_upload(file, max_bytes=MAX_UPLOAD_BYTES, allowed_exts=PRINT_COPY_EXTS)
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="That file is not a PDF. In Word or Excel use File, Save as, PDF, then add that file.")
    name = re.split(r"[\\/]", file.filename or "print copy.pdf")[-1] or "print copy.pdf"
    path = f"print-copies/{doc_id}/{uuid_module.uuid4()}.pdf"
    try:
        supabase.storage.from_(BUCKET).upload(path, content, {"content-type": "application/pdf"})
        url = supabase.storage.from_(BUCKET).get_public_url(path)
    except Exception as e:
        _drop_stored(path)
        raise server_error(e, "Storing the print copy")
    changes = {"print_copy_path": path, "print_copy_url": url, "print_copy_name": name, "print_copy_size": len(content)}
    touch(doc, changes)
    try:
        supabase.table("documents").update(changes).eq("id", doc_id).execute()
    except Exception as e:
        _drop_stored(path)
        raise server_error(e, "Saving the print copy")
    _drop_stored(doc.get("print_copy_path"))
    log_activity(supabase, current_user, "update", doc_id, name=doc["name"], print_copy=name)
    return shown({**doc, **changes})


@router.delete("/{doc_id}/print-copy")
async def remove_print_copy(doc_id: str, current_user: dict = Depends(get_current_user)):
    """Stop keeping a print copy with this file; Print goes back to drawing the file itself."""
    require_upgrade(supabase)
    doc = _visible_doc(doc_id, current_user)
    if not doc.get("print_copy_path"):
        return shown(doc)
    changes = dict(NO_PRINT_COPY)
    touch(doc, changes)
    try:
        supabase.table("documents").update(changes).eq("id", doc_id).execute()
    except Exception as e:
        raise server_error(e, "Removing the print copy")
    _drop_stored(doc.get("print_copy_path"))
    log_activity(supabase, current_user, "update", doc_id, name=doc["name"], print_copy="removed")
    return shown({**doc, **changes})


# ── History ──────────────────────────────────────────────────────────────────────────────────────────────────────────

@router.get("/{doc_id}/activity")
async def document_activity(doc_id: str, current_user: dict = Depends(get_current_user)):
    require_upgrade(supabase)
    if not is_uuid(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    if viewer_scope(current_user) is not None:
        assert_row_visible(current_user, "documents", _get_doc(doc_id), supabase, detail="Document not found")
    try:
        rows = response_rows(supabase.table("document_activities").select("id,action,details,created_at").eq("document_id", doc_id).order("created_at", desc=True).limit(50).execute())
        return rows
    except Exception as e:
        raise server_error(e, "Loading the history")


# ── Reading the words out of files that were uploaded before this existed ────────────────────────────────────────────

@router.post("/reindex")
async def reindex_documents(limit: int = Query(5, ge=1, le=20), current_user: dict = Depends(require_role("manager"))):
    """Read the text of a few files that have not been read yet, so they can be found by what they say. The page calls this
    until `remaining` is 0. A file that cannot be read is marked done anyway and found by its name and details."""
    require_upgrade(supabase)
    if viewer_scope(current_user) is not None:
        raise HTTPException(status_code=403, detail="Reindexing is not available to section-limited accounts.")
    try:
        pending = supabase.table("documents").select("id,storage_path,ext,file_size", count=CountMethod.exact).is_("text_indexed_at", "null").is_("deleted_at", "null").order("id").limit(limit).execute()
        processed = 0
        for doc in response_rows(pending):
            text = ""
            if doc.get("storage_path") and int(doc.get("file_size") or 0) <= MAX_EXTRACT_BYTES:
                try:
                    data = await run_in_threadpool(lambda p=doc["storage_path"]: supabase.storage.from_(BUCKET).download(p))
                    text = await run_in_threadpool(extract_text, data, str(doc.get("ext") or ""))
                except Exception as e:
                    logger.warning("Could not read %s for indexing: %s", doc["id"], e)
            supabase.table("documents").update({"content_text": text, "text_indexed_at": _now()}).eq("id", doc["id"]).execute()
            processed += 1
        total = pending.count if isinstance(pending.count, int) else processed
        return {"processed": processed, "remaining": max(total - processed, 0)}
    except Exception as e:
        raise server_error(e, "Indexing documents")
