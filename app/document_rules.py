# backend/app/document_rules.py — the pure rules of the document library: what kind of file something is, which names are
# acceptable, how a search is understood, how the folder tree is walked, and what text is read out of a file so it can be
# found by what it says. Nothing here touches the database or the network, so every rule has a plain test.
import html
import io
import logging
import mimetypes
import re
import zipfile
from hashlib import sha256
from typing import Dict, Iterable, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# ── File kinds ───────────────────────────────────────────────────────────────────────────────────────────────────────
# One list decides three things: the kind shown and filtered on, the extensions an upload may have, and the content type a
# file is served with. Executables and scripts (.exe, .bat, .sh, .js, .html ...) are deliberately absent.
EXT_GROUPS: Dict[str, Set[str]] = {
    "pdf": {"pdf"},
    "document": {"doc", "docx", "rtf", "odt"},
    "spreadsheet": {"xls", "xlsx", "xlsm", "csv", "tsv", "ods"},
    "presentation": {"ppt", "pptx", "pps", "ppsx", "odp"},
    "text": {"txt", "md", "log", "json", "xml"},
    "image": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "svg", "heic", "heif", "avif", "ico"},
    "audio": {"mp3", "wav", "m4a", "ogg", "oga", "aac", "flac", "opus", "wma", "amr"},
    "video": {"mp4", "mov", "avi", "wmv", "mkv", "webm", "m4v", "3gp", "mpg", "mpeg"},
    "archive": {"zip", "rar", "7z", "tar", "gz"},
    "email": {"eml", "msg"},
    "drawing": {"dwg", "dxf"},
}
FILE_TYPES: List[str] = list(EXT_GROUPS) + ["file"]
HUB_UPLOAD_EXTS: Set[str] = set().union(*EXT_GROUPS.values())
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

_EXT_TO_TYPE = {ext: kind for kind, exts in EXT_GROUPS.items() for ext in exts}

# The browser's own claim about a file's type is not trusted: a ".txt" sent as "text/html" would be served as a web page
# from the storage address. The type a file is served with comes from its extension.
_CONTENT_TYPES = {
    "pdf": "application/pdf", "doc": "application/msword", "rtf": "application/rtf", "csv": "text/csv", "tsv": "text/tab-separated-values",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xls": "application/vnd.ms-excel",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "ppt": "application/vnd.ms-powerpoint",
    "txt": "text/plain", "md": "text/plain", "log": "text/plain", "json": "application/json", "xml": "application/xml",
    "svg": "image/svg+xml", "heic": "image/heic", "heif": "image/heif", "avif": "image/avif", "ico": "image/x-icon",
    "m4a": "audio/mp4", "opus": "audio/ogg", "amr": "audio/amr", "wma": "audio/x-ms-wma", "mkv": "video/x-matroska",
    "eml": "message/rfc822", "msg": "application/vnd.ms-outlook", "dwg": "application/acad", "dxf": "application/dxf",
    "7z": "application/x-7z-compressed", "rar": "application/vnd.rar", "gz": "application/gzip",
}


def ext_of(filename: Optional[str]) -> str:
    name = (filename or "").strip().lower()
    return name.rsplit(".", 1)[-1] if "." in name.strip(".") else ""


def file_type_of(filename: Optional[str]) -> str:
    return _EXT_TO_TYPE.get(ext_of(filename), "file")


def content_type_for(filename: Optional[str]) -> str:
    ext = ext_of(filename)
    return _CONTENT_TYPES.get(ext) or mimetypes.types_map.get(f".{ext}") or "application/octet-stream"


def content_hash(content: bytes) -> str:
    return sha256(content).hexdigest()


# ── Names ────────────────────────────────────────────────────────────────────────────────────────────────────────────
MAX_NAME_LENGTH = 120
MAX_FOLDER_DEPTH = 8
PATH_SEPARATOR = " > "


def _tidy(text: str) -> str:
    return " ".join((text or "").split())


def clean_folder_name(name: Optional[str]) -> str:
    """The folder name to save, or ValueError with the reason in words the user can act on."""
    tidy = _tidy(name or "")
    if not tidy:
        raise ValueError("Folder name is required")
    if len(tidy) > MAX_NAME_LENGTH:
        raise ValueError(f"A folder name can be at most {MAX_NAME_LENGTH} characters")
    if re.search(r"[\x00-\x1f\x7f\\]", tidy) or not tidy.strip("."):
        raise ValueError("That folder name is not allowed")
    return tidy


def clean_display_name(name: Optional[str], fallback: str) -> str:
    tidy = _tidy(name or "") or _tidy(fallback) or "Untitled"
    return tidy[:200]


def unique_display_name(wanted: str, taken: Iterable[str]) -> str:
    """`wanted`, or "wanted (2)", "wanted (3)" ... when a file with that name is already in the folder. The extension stays
    at the end, so "Report.pdf" becomes "Report (2).pdf"."""
    used = {t.lower() for t in taken}
    if wanted.lower() not in used:
        return wanted
    stem, dot, ext = wanted.rpartition(".")
    if not dot or not stem or not ext.isalnum() or len(ext) > 6:
        stem, dot, ext = wanted, "", ""
    n = 2
    while True:
        candidate = f"{stem} ({n}){dot}{ext}"
        if candidate.lower() not in used:
            return candidate
        n += 1


MAX_TAGS = 20
MAX_TAG_LENGTH = 40


def normalise_tags(tags: Optional[Iterable[str]]) -> List[str]:
    """Tags saved the same way however they were typed: lower case, one space between words, no repeats, in the order
    given. ValueError when there are too many or one is too long."""
    seen: List[str] = []
    for raw in tags or []:
        tag = _tidy(str(raw)).lower().strip(",")
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise ValueError(f"A tag can be at most {MAX_TAG_LENGTH} characters")
        if tag not in seen:
            seen.append(tag)
    if len(seen) > MAX_TAGS:
        raise ValueError(f"A file can have at most {MAX_TAGS} tags")
    return seen


# ── Search ───────────────────────────────────────────────────────────────────────────────────────────────────────────
MAX_TERMS = 8
MAX_TERM_LENGTH = 80


def search_terms(query: Optional[str]) -> List[str]:
    """The words of a search. A phrase in double quotes stays together; every term must match (see ilike_value)."""
    terms: List[str] = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', query or ""):
        term = _tidy(phrase or word).lower().strip('"')[:MAX_TERM_LENGTH]
        if term and term not in terms:
            terms.append(term)
    return terms[:MAX_TERMS]


def ilike_value(term: str) -> str:
    """The pattern for `column.ilike.<pattern>` inside a PostgREST or(...) filter, matching `term` anywhere.

    Two layers of escaping, each for a reason. The LIKE layer makes "%" and "_" in what a person typed mean themselves
    instead of "anything". The PostgREST layer quotes the value, so a comma or bracket the person typed cannot end the
    filter early or add one of its own.
    """
    like = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{like}%"
    return '"' + pattern.replace("\\", "\\\\").replace('"', '\\"') + '"'


def csv_values(value: Optional[str]) -> List[str]:
    return [v for v in (part.strip() for part in (value or "").split(",")) if v]


def valid_types(value: Optional[str]) -> List[str]:
    """The kinds asked for, silently dropping any that do not exist."""
    return [t for t in csv_values(value) if t in FILE_TYPES]


SORTS = {"name": ("name", False), "date": ("created_at", True), "updated": ("updated_at", True),
         "size": ("file_size", True), "type": ("file_type", False)}


def sort_spec(sort: Optional[str], direction: Optional[str]) -> tuple:
    """(column, descending) for a sort name and an optional "asc" / "desc"."""
    column, descending = SORTS.get(sort or "", SORTS["date"])
    if direction in ("asc", "desc"):
        descending = direction == "desc"
    return column, descending


# ── The folder tree ──────────────────────────────────────────────────────────────────────────────────────────────────
def children_map(folders: Sequence[dict]) -> Dict[Optional[str], List[dict]]:
    out: Dict[Optional[str], List[dict]] = {}
    for folder in folders:
        parent = str(folder["parent_id"]) if folder.get("parent_id") else None
        out.setdefault(parent, []).append(folder)
    return out


def descendant_ids(folders: Sequence[dict], folder_id: str) -> List[str]:
    """`folder_id` and every folder below it. Stops on a loop instead of following it forever."""
    kids = children_map(folders)
    found: List[str] = []
    stack = [str(folder_id)]
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.append(current)
        stack.extend(str(child["id"]) for child in kids.get(current, []))
    return found


def ancestry(folders: Sequence[dict], folder_id: Optional[str]) -> List[dict]:
    """The folders from the top down to `folder_id`, itself included. Empty for no folder or an unknown one."""
    by_id = {str(f["id"]): f for f in folders}
    chain: List[dict] = []
    current = by_id.get(str(folder_id)) if folder_id else None
    while current is not None and len(chain) <= MAX_FOLDER_DEPTH * 4:
        chain.append(current)
        parent = current.get("parent_id")
        current = by_id.get(str(parent)) if parent else None
    return list(reversed(chain))


def folder_path(folders: Sequence[dict], folder_id: Optional[str]) -> str:
    return PATH_SEPARATOR.join(str(f["name"]) for f in ancestry(folders, folder_id))


def subtree_height(folders: Sequence[dict], folder_id: str) -> int:
    """How many levels the folder and everything below it take up (a folder with no children is 1)."""
    kids = children_map(folders)

    def height(current: str, seen: frozenset) -> int:
        if current in seen:
            return 0
        below = [height(str(child["id"]), seen | {current}) for child in kids.get(current, [])]
        return 1 + (max(below) if below else 0)

    return height(str(folder_id), frozenset())


def check_placement(folders: Sequence[dict], folder_id: Optional[str], new_parent_id: Optional[str]) -> None:
    """ValueError when moving `folder_id` under `new_parent_id` (or creating one there, folder_id None) is not allowed: into
    itself or one of its own subfolders, or deeper than the tree is allowed to go."""
    if new_parent_id is None:
        return
    if folder_id is not None and str(new_parent_id) in descendant_ids(folders, str(folder_id)):
        raise ValueError("A folder cannot be moved into itself or one of its own subfolders")
    levels_above = len(ancestry(folders, new_parent_id))
    levels_moving = subtree_height(folders, str(folder_id)) if folder_id is not None else 1
    if levels_above + levels_moving > MAX_FOLDER_DEPTH:
        raise ValueError(f"Folders can be at most {MAX_FOLDER_DEPTH} levels deep")


def name_taken(folders: Sequence[dict], parent_id: Optional[str], name: str, ignore_id: Optional[str] = None) -> bool:
    wanted = name.lower()
    for folder in children_map(folders).get(str(parent_id) if parent_id else None, []):
        if str(folder["id"]) != str(ignore_id) and str(folder["name"]).lower() == wanted:
            return True
    return False


# ── Reading the words out of a file ──────────────────────────────────────────────────────────────────────────────────
# Searching inside files is what makes a document findable by what it says, not only by what it was named. It is kept
# modest on purpose: the first part of each file, capped, so the database does not grow without bound. A file that cannot
# be read (scanned pages, a password, an old binary format) is simply found by its name and details.
MAX_TEXT_CHARS = 30_000
_MAX_MEMBER_BYTES = 40 * 1024 * 1024
_PLAIN = {"txt", "md", "log", "csv", "tsv", "json", "xml", "eml"}
_XML_TAG = re.compile(r"<[^>]+>")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _from_office_xml(content: bytes, members: Sequence[str], paragraph_end: str) -> str:
    out: List[str] = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in members:
            try:
                info = archive.getinfo(name)
            except KeyError:
                continue
            if info.file_size > _MAX_MEMBER_BYTES:
                continue
            xml = archive.read(name).decode("utf-8", errors="replace")
            out.append(html.unescape(_XML_TAG.sub("", xml.replace(paragraph_end, " "))))
            if sum(len(part) for part in out) >= MAX_TEXT_CHARS * 2:
                break
    return " ".join(out)


def _pptx_slides(content: bytes) -> List[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
    return sorted(names, key=lambda n: int(re.findall(r"\d+", n)[0]))


def _pdf_text(content: bytes) -> str:
    import pymupdf  # imported here: only an upload of a PDF needs it

    parts: List[str] = []
    total = 0
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        for page in pdf:
            text = str(page.get_text())
            parts.append(text)
            total += len(text)
            if total >= MAX_TEXT_CHARS * 2 or (page.number or 0) >= 300:
                break
    return " ".join(parts)


def _xlsx_text(content: bytes) -> str:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    parts: List[str] = []
    try:
        for sheet in workbook.worksheets[:20]:
            parts.append(str(sheet.title))
            for row_number, row in enumerate(sheet.iter_rows(values_only=True)):
                if row_number >= 5000:
                    break
                parts.extend(str(cell) for cell in row if cell not in (None, ""))
            if sum(len(p) for p in parts) >= MAX_TEXT_CHARS * 2:
                break
    finally:
        workbook.close()
    return " ".join(parts)


def extract_text(content: bytes, ext: str, limit: int = MAX_TEXT_CHARS) -> str:
    """The readable words of a file (empty when it has none we can read). Never raises: a file nobody can read is still
    a file that has to be saved."""
    try:
        ext = (ext or "").lower()
        if ext == "pdf":
            text = _pdf_text(content)
        elif ext == "docx":
            text = _from_office_xml(content, ["word/document.xml"], "</w:p>")
        elif ext == "pptx":
            text = _from_office_xml(content, _pptx_slides(content), "</a:p>")
        elif ext in ("xlsx", "xlsm"):
            text = _xlsx_text(content)
        elif ext in _PLAIN:
            text = content[:2_000_000].decode("utf-8", errors="replace")
        else:
            return ""
        return _collapse(text)[:limit]
    except Exception as exc:  # a damaged, encrypted or unusual file: found by its name instead
        logger.info("No text read from a .%s file: %s", ext, exc)
        return ""
