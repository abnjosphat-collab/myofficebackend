# app/routers/document_folders.py — the document library's folders: create, rename, move, delete, and "set up the AMS
# folders". The files are in documents.py; the rules (names, depth, no loops, the AMS structure) are pure code in
# document_rules.py and document_structure.py, so this file only carries them out.
#
# Every folder has the same facilities: a folder made by hand, one from the AMS structure and one in the middle of it can
# all be renamed, moved, given files and deleted (when empty). Deleting a folder never deletes files: a folder that still
# holds files, trashed ones included, is refused with the count.
import logging
from typing import Any, Dict, List, Optional

from postgrest.types import CountMethod
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user, require_role
from app.document_rules import (
    check_placement, clean_folder_name, descendant_ids, folder_path, name_taken,
)
from app.document_structure import CUSTOM_SORT_ORDER, plan_seed, summarise
from app.documents_support import QUICK_PRINT_KEY, get_folder, is_uuid, load_folders, log_activity, require_upgrade
from app.db_helpers import response_rows
from app.errors import server_error
from app.scope import scope_root_id, scope_subtree_ids, viewer_scope, scope_value
from app.supabase_client import supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])


def _actor(user: Dict[str, Any]) -> str:
    return user.get("name") or user.get("email") or ""


def _refresh_paths(folders: List[Dict[str, Any]], folder_ids: List[str]) -> None:
    """Files carry their folder's display path (it is searched). After a rename or move, bring the files below up to date."""
    for folder_id in folder_ids:
        supabase.table("documents").update({"folder_path": folder_path(folders, folder_id)}).eq("folder_ref", folder_id).execute()


class FolderCreate(BaseModel):
    parent_id: Optional[str] = None
    name: str
    description: str = ""


class FolderUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None  # sent as null it means "move to the top level"; not sent, it stays where it is
    description: Optional[str] = None


@router.post("/folders")
async def create_folder(body: FolderCreate, current_user: dict = Depends(get_current_user)):
    require_upgrade(supabase)
    try:
        name = clean_folder_name(body.name)
        folders = await run_in_threadpool(lambda: load_folders(supabase))
        parent = body.parent_id or None
        scope = viewer_scope(current_user)
        if scope is not None and parent is None and name.lower() != (scope_value(scope) or "").lower():
            # Self-service root only: a scoped account may create the
            # top-level folder named for its scope, nothing else up there.
            detail = (f"Create a top-level folder named {scope_value(scope)} first."
                      if scope_root_id(folders, scope) is None else f"New folders go inside {scope_value(scope)}.")
            raise HTTPException(status_code=404, detail=detail)
        if parent:
            get_folder(supabase, parent, current_user, folders)
        check_placement(folders, None, parent)
        if name_taken(folders, parent, name):
            raise HTTPException(status_code=409, detail="A folder with this name already exists here.")
        made = response_rows(supabase.table("document_folders").insert({
            "parent_id": parent, "name": name, "description": body.description.strip(), "sort_order": CUSTOM_SORT_ORDER,
            "category_id": "", "category_name": "", "created_by": _actor(current_user),
        }).execute())[0]
        log_activity(supabase, current_user, "folder_create", None, folder=name, folder_id=made["id"])
        return made
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise server_error(e, "Creating the folder")


@router.put("/folders/{folder_id}")
async def update_folder(folder_id: str, body: FolderUpdate, current_user: dict = Depends(get_current_user)):
    require_upgrade(supabase)
    try:
        if not is_uuid(folder_id):
            raise HTTPException(status_code=404, detail="That folder no longer exists. Reload the page.")
        folders = await run_in_threadpool(lambda: load_folders(supabase))
        current = next((f for f in folders if str(f["id"]) == folder_id), None)
        if current is None:
            raise HTTPException(status_code=404, detail="That folder no longer exists. Reload the page.")
        scope = viewer_scope(current_user)
        sub = scope_subtree_ids(folders, scope) if scope is not None else []
        if scope is not None and str(current["id"]) not in sub:
            raise HTTPException(status_code=404, detail="That folder no longer exists. Reload the page.")
        root = scope is not None and str(current["id"]) == scope_root_id(folders, scope)

        changes: Dict[str, Any] = {}
        name = clean_folder_name(body.name) if body.name is not None else current["name"]
        parent = (body.parent_id or None) if "parent_id" in body.model_fields_set else (str(current["parent_id"]) if current.get("parent_id") else None)
        moved = parent != (str(current["parent_id"]) if current.get("parent_id") else None)
        if name != current["name"]:
            changes["name"] = name
        if scope is not None and root and name.lower() != (scope_value(scope) or "").lower():
            # The root keeps the scope's name: renaming it away would orphan
            # the whole subtree from its viewers.
            raise HTTPException(status_code=404, detail=f"The {scope_value(scope)} folder keeps its name.")
        if moved:
            if parent:
                get_folder(supabase, parent, current_user, folders)
            if scope is not None and (root or parent is None or parent not in sub):
                # Stay-or-scope: a scoped move lands inside the subtree, and
                # the root itself stays where the scope can find it.
                detail = f"The {scope_value(scope)} folder stays at the top level." if root else "That folder no longer exists. Reload the page."
                raise HTTPException(status_code=404, detail=detail)
            check_placement(folders, folder_id, parent)
            changes["parent_id"] = parent
        if body.description is not None:
            changes["description"] = body.description.strip()
        if not changes:
            return current
        if ("name" in changes or moved) and name_taken(folders, parent, name, ignore_id=folder_id):
            raise HTTPException(status_code=409, detail="A folder with this name already exists here.")

        saved = response_rows(supabase.table("document_folders").update(changes).eq("id", folder_id).execute())
        if not saved:
            raise HTTPException(status_code=404, detail="That folder no longer exists. Reload the page.")
        if "name" in changes or moved:
            after = [{**f, **changes} if str(f["id"]) == folder_id else f for f in folders]
            _refresh_paths(after, descendant_ids(after, folder_id))
        log_activity(supabase, current_user, "folder_move" if moved else "folder_update", None, folder=name, previous=current["name"] if "name" in changes else None)
        return saved[0]
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise server_error(e, "Updating the folder")


def _count_files(folder_id: str, trashed: bool) -> int:
    query = supabase.table("documents").select("id", count=CountMethod.exact).eq("folder_ref", folder_id)
    query = query.not_.is_("deleted_at", "null") if trashed else query.is_("deleted_at", "null")
    result = query.limit(1).execute()
    return result.count if isinstance(result.count, int) else len(response_rows(result))


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, current_user: dict = Depends(require_role("manager"))):
    require_upgrade(supabase)
    try:
        folder = get_folder(supabase, folder_id, current_user)
        if folder.get("seed_key") == QUICK_PRINT_KEY:
            raise HTTPException(status_code=409, detail="Quick print is kept for the papers you print often. Move or delete the papers in it instead.")
        folders = await run_in_threadpool(lambda: load_folders(supabase))
        subfolders = [f for f in folders if str(f.get("parent_id")) == folder_id]
        if subfolders:
            raise HTTPException(status_code=409, detail=f"“{folder['name']}” still has {len(subfolders)} {'subfolder' if len(subfolders) == 1 else 'subfolders'}. Move or delete {'it' if len(subfolders) == 1 else 'them'} first.")
        files, trashed = _count_files(folder_id, False), _count_files(folder_id, True)
        if files:
            raise HTTPException(status_code=409, detail=f"“{folder['name']}” still holds {files} {'file' if files == 1 else 'files'}. Move {'it' if files == 1 else 'them'} to another folder first; deleting a folder never deletes files.")
        if trashed:
            raise HTTPException(status_code=409, detail=f"“{folder['name']}” still has {trashed} {'file' if trashed == 1 else 'files'} in the trash. Restore or delete {'it' if trashed == 1 else 'them'} for good first.")
        supabase.table("document_folders").delete().eq("id", folder_id).execute()
        log_activity(supabase, current_user, "folder_delete", None, folder=folder["name"])
        return {"ok": True}
    except Exception as e:
        raise server_error(e, "Deleting the folder")


@router.post("/folders/seed-ams")
async def seed_ams_folders(current_user: dict = Depends(require_role("manager"))):
    """Create the Asset Management System folder structure. Folders that already exist are skipped (found by their key, so
    a renamed one is still recognised); a folder made by hand under the same name is adopted with its files. Safe to press
    again: the second time, nothing is created."""
    require_upgrade(supabase)
    if viewer_scope(current_user) is not None:
        raise HTTPException(status_code=403, detail="Setting up the AMS folders is not available to section-limited accounts.")
    try:
        folders = await run_in_threadpool(lambda: load_folders(supabase))
        steps = plan_seed(folders)
        ids: Dict[str, str] = {s.key: s.folder_id for s in steps if s.folder_id}

        adopted: List[str] = []
        for step in steps:
            if step.action == "adopt":
                supabase.table("document_folders").update({"name": step.name, "seed_key": step.key, "sort_order": step.sort_order}).eq("id", step.folder_id).execute()
                adopted.append(str(step.folder_id))

        by_depth: Dict[int, list] = {}
        for step in steps:
            if step.action == "create":
                by_depth.setdefault(step.depth, []).append(step)
        for depth in sorted(by_depth):
            rows = []
            for step in by_depth[depth]:
                parent = ids.get(step.parent_key) if step.parent_key else None
                if step.parent_key and not parent:
                    raise RuntimeError(f"parent of {step.key} was not created")
                rows.append({"name": step.name, "parent_id": parent, "seed_key": step.key, "sort_order": step.sort_order,
                             "category_id": "", "category_name": "", "created_by": _actor(current_user)})
            for made in response_rows(supabase.table("document_folders").insert(rows).execute()):
                ids[made["seed_key"]] = made["id"]

        if adopted:
            after = await run_in_threadpool(lambda: load_folders(supabase))
            below = sorted({i for a in adopted for i in descendant_ids(after, a)})
            _refresh_paths(after, below)
        summary = summarise(steps)
        log_activity(supabase, current_user, "folder_seed", None, **summary)
        return summary
    except Exception as e:
        raise server_error(e, "Setting up the AMS folders")
