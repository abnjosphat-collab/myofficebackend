# backend/app/documents_support.py — what the document routers share: the upgrade check, reading folders, the activity log,
# the search filters, and how a stored row is shown. The pure rules (names, file kinds, search terms, the folder tree) are in
# document_rules.py; this file is the part that speaks to the database.
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException

from app.audit import has_columns
from app.db_helpers import fetch_all_pages, response_rows
from app.errors import server_error
from app.document_rules import (
    csv_values, ilike_value, valid_types,
)

logger = logging.getLogger(__name__)

UPGRADE_MESSAGE = (
    "The document library needs its one-time upgrade. Ask the administrator to run "
    "supabase_migration_documents_v2.sql in the Supabase SQL editor, then reload this page."
)

# Everything a list needs. The words read out of a file (content_text) and the combined search text are large and only
# ever searched, never sent to the browser.
LIST_COLUMNS = (
    "id,created_at,updated_at,name,original_name,file_url,file_size,mime_type,file_type,ext,folder_ref,folder_path,"
    "starred,description,tags,uploaded_by,deleted_at,deleted_by,text_indexed_at"
)
FOLDER_COLUMNS = "id,parent_id,name,sort_order,seed_key,description,created_at,updated_at,created_by"
_HIDDEN = ("content_text", "search_text", "content_hash", "storage_path", "category_id", "category_name", "folder_id")

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID.match(value))


_NEW_COLUMNS = (
    ("documents", ["folder_ref", "deleted_at", "tags", "content_hash", "search_text"]),
    ("document_folders", ["parent_id", "seed_key", "sort_order"]),
)


def _columns_missing(db, table: str, columns: List[str]) -> bool:
    """True when the database says a column is not there. Any other failure (a dropped connection) is raised, not read as
    "not upgraded": telling someone to run SQL they already ran, during an outage, would only add confusion."""
    try:
        db.table(table).select(",".join(columns)).limit(1).execute()
        return False
    except Exception as exc:
        text = str(exc)
        if "42703" in text or "PGRST204" in text or "does not exist" in text or "Could not find" in text:
            return True
        raise


def require_upgrade(db) -> None:
    """409 with the plain instruction until supabase_migration_documents_v2.sql has been run. Cheap once it has: a yes is
    remembered for the life of the process, a no is re-checked after a minute (so running the SQL needs no restart)."""
    if all(has_columns(db, table, columns) for table, columns in _NEW_COLUMNS):
        return
    try:
        missing = any(_columns_missing(db, table, columns) for table, columns in _NEW_COLUMNS)
    except Exception as exc:
        raise server_error(exc, "Checking the document library")
    if missing:
        raise HTTPException(status_code=409, detail=UPGRADE_MESSAGE)


def shown(row: Dict[str, Any]) -> Dict[str, Any]:
    """A stored document as the browser gets it: no bulk text, no hash, no internal storage path or legacy columns."""
    return {k: v for k, v in row.items() if k not in _HIDDEN}


def load_folders(db) -> List[Dict[str, Any]]:
    return fetch_all_pages(
        lambda start, end: db.table("document_folders").select(FOLDER_COLUMNS).order("sort_order").order("name").order("id").range(start, end).execute()
    )


def get_folder(db, folder_id: Optional[str]) -> Dict[str, Any]:
    """The folder row, or 404/422. A folder id from the browser is checked here, never trusted."""
    if not is_uuid(folder_id):
        raise HTTPException(status_code=422, detail="Choose a folder from the list.")
    found = response_rows(db.table("document_folders").select(FOLDER_COLUMNS).eq("id", folder_id).execute())
    if not found:
        raise HTTPException(status_code=404, detail="That folder no longer exists. Reload the page.")
    return found[0]


def log_activity(db, user: Dict[str, Any], action: str, document_id: Optional[str] = None, **details: Any) -> None:
    """One line of history (who did what to which file). Best effort and loud when it fails: a missing history line must
    not undo a file move that already happened, but it is logged so it cannot go unnoticed."""
    try:
        row: Dict[str, Any] = {
            "action": action,
            "details": {"by": user.get("name") or user.get("email") or "", **{k: v for k, v in details.items() if v is not None}},
        }
        if document_id:
            row["document_id"] = document_id
        if is_uuid(user.get("user_id")):
            row["user_id"] = user["user_id"]
        db.table("document_activities").insert(row).execute()
    except Exception as exc:
        logger.error("Could not record %s in the document history: %s", action, exc)


# ── Search filters ───────────────────────────────────────────────────────────────────────────────────────────────────

def _like(term: str) -> str:
    """A %term% pattern for a plain .ilike() filter (no quoting needed there, "%" and "_" still mean themselves)."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def apply_filters(query, *, terms: Iterable[str], folder_ids: Optional[List[str]], unfiled: bool, trashed: bool,
                  types: Optional[str], exts: Optional[str], starred: bool, added_from: Optional[str],
                  added_to: Optional[str], min_size: Optional[int], max_size: Optional[int], tags: Optional[str],
                  uploader: Optional[str], names_only: bool = False):
    """Narrow a documents query. Every filter given must hold, and every search term must be found somewhere: in the
    file's details, or in the words read out of it."""
    query = query.not_.is_("deleted_at", "null") if trashed else query.is_("deleted_at", "null")
    if unfiled:
        query = query.is_("folder_ref", "null")
    elif folder_ids is not None:
        query = query.in_("folder_ref", folder_ids)
    kinds = valid_types(types)
    if kinds:
        query = query.in_("file_type", kinds)
    extensions = [e.lower().lstrip(".") for e in csv_values(exts)]
    if extensions:
        query = query.in_("ext", extensions)
    if starred:
        query = query.eq("starred", True)
    if added_from:
        query = query.gte("created_at", f"{added_from}T00:00:00+00:00")
    if added_to:
        query = query.lte("created_at", f"{added_to}T23:59:59.999999+00:00")
    if min_size is not None:
        query = query.gte("file_size", min_size)
    if max_size is not None:
        query = query.lte("file_size", max_size)
    wanted_tags = [t.strip().lower() for t in csv_values(tags)]
    if wanted_tags:
        query = query.contains("tags", wanted_tags)
    if uploader and uploader.strip():
        query = query.ilike("uploaded_by", _like(uploader.strip()))
    for term in terms:
        value = ilike_value(term)
        columns = ["search_text"] if names_only else ["search_text", "content_text"]
        query = query.or_(",".join(f"{c}.ilike.{value}" for c in columns))
    return query


def relevance(row: Dict[str, Any], terms: List[str]) -> int:
    """How well a file answers a search, for ordering: a word in the name counts most, then tags, then the rest of its
    details. A file found only by what is written inside it scores 0."""
    if not terms:
        return 0
    name = f"{row.get('name', '')} {row.get('original_name', '')}".lower()
    tags = " ".join(row.get("tags") or []).lower()
    detail = f"{row.get('description', '')} {row.get('folder_path', '')}".lower()
    return sum((6 if t in name else 0) + (3 if t in tags else 0) + (1 if t in detail else 0) for t in terms)


def found_inside(row: Dict[str, Any], terms: List[str]) -> bool:
    """True when a search term was matched only by the words inside the file, not by anything in its name or details, so
    the browser can say "found inside the document"."""
    if not terms:
        return False
    meta = " ".join(str(row.get(k) or "") for k in ("name", "original_name", "description", "folder_path", "ext", "uploaded_by")).lower()
    meta += " " + " ".join(row.get("tags") or []).lower()
    return any(t not in meta for t in terms)
