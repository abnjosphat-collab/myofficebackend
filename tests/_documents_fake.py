# tests/_documents_fake.py — an in-memory stand-in for the Supabase client (tables and one storage bucket) for the document
# routers. It evaluates what the routers really send: eq / is_ / not_.is_ / in_ / gte / lte / ilike / contains, the quoted
# and escaped `or_` strings (so a hostile search term is tested against the real escaping), order / range / limit, count="exact",
# explicit column lists (so a hidden column really is hidden), and the database's unique rules, raised the way Postgres raises them.
import copy
import re
import uuid
from typing import Any, Callable, Dict, List, Optional


class PgError(Exception):
    """What supabase-py raises for a Postgres refusal: carries the SQLSTATE `code` that app.errors maps to a 409/422."""

    def __init__(self, code: str, message: str, details: str = ""):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


class Result:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


def like_to_regex(pattern: str) -> "re.Pattern[str]":
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            out += re.escape(pattern[i + 1])
            i += 2
            continue
        out += ".*" if c == "%" else "." if c == "_" else re.escape(c)
        i += 1
    return re.compile(f"^{out}$", re.IGNORECASE | re.DOTALL)


def _split_top_level(expr: str) -> List[str]:
    parts, buf, quoted, i = [], "", False, 0
    while i < len(expr):
        c = expr[i]
        if c == "\\" and quoted and i + 1 < len(expr):
            buf += expr[i:i + 2]
            i += 2
            continue
        if c == '"':
            quoted = not quoted
        if c == "," and not quoted:
            parts.append(buf)
            buf = ""
        else:
            buf += c
        i += 1
    parts.append(buf)
    return parts


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


class Query:
    def __init__(self, db: "FakeDb", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.cols, self.want_count = "select", None, "*", False
        self.preds: List[Callable[[dict], bool]] = []
        self.orders: List[tuple] = []
        self.span: Optional[tuple] = None
        self.max_rows: Optional[int] = None
        self.single = False
        self._negate = False

    # selecting
    def select(self, cols="*", count=None):
        self.cols, self.want_count = cols, count == "exact"
        return self

    @property
    def not_(self):
        self._negate = True
        return self

    def eq(self, col, val):
        self.preds.append(lambda r: r.get(col) == val)
        return self

    def neq(self, col, val):
        self.preds.append(lambda r: r.get(col) != val)
        return self

    def is_(self, col, val):
        want_null = str(val).lower() == "null"
        negate, self._negate = self._negate, False
        self.preds.append(lambda r: ((r.get(col) is None) == want_null) != negate)
        return self

    def in_(self, col, values):
        values = list(values)
        self.preds.append(lambda r: r.get(col) in values)
        return self

    @staticmethod
    def _cmp(a, b):
        """Numbers compare as numbers, everything else (ISO dates) as text, as Postgres would."""
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return (a > b) - (a < b)
        return (str(a) > str(b)) - (str(a) < str(b))

    def gte(self, col, val):
        self.preds.append(lambda r: r.get(col) is not None and self._cmp(r[col], val) >= 0)
        return self

    def lte(self, col, val):
        self.preds.append(lambda r: r.get(col) is not None and self._cmp(r[col], val) <= 0)
        return self

    def ilike(self, col, pattern):
        rx = like_to_regex(pattern)
        self.preds.append(lambda r: r.get(col) is not None and bool(rx.match(str(r[col]))))
        return self

    def contains(self, col, values):
        self.preds.append(lambda r: set(values) <= set(r.get(col) or []))
        return self

    def or_(self, expr):
        branches = []
        for part in _split_top_level(expr):
            col, op, value = part.split(".", 2)
            assert op == "ilike", f"the fake only knows ilike inside or_, got {op}"
            branches.append((col, like_to_regex(_unquote(value))))
        self.preds.append(lambda r: any(rx.match(str(r.get(col) or "")) for col, rx in branches))
        return self

    def order(self, col, desc=False):
        self.orders.append((col, desc))
        return self

    def limit(self, n):
        self.max_rows = n
        return self

    def range(self, start, end):
        self.span = (start, end)
        return self

    def maybe_single(self):
        self.single = True
        return self

    # writing
    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _project(self, row: dict) -> dict:
        if self.cols in ("*", "", None):
            return copy.deepcopy(row)
        wanted = [c.strip() for c in self.cols.split(",")]
        return {c: copy.deepcopy(row.get(c)) for c in wanted}

    def execute(self):
        db = self.db
        db.calls.append((self.op, self.table))
        if db.fail or self.op in db.fail_ops:
            raise RuntimeError("database unavailable")
        if self.op == "select":
            for column in [c.strip() for c in str(self.cols).split(",") if c.strip() and c.strip() != "*"]:
                if column in db.missing.get(self.table, ()):
                    raise PgError("42703", f"column {self.table}.{column} does not exist")
        rows = db.tables.setdefault(self.table, [])

        if self.op == "insert":
            batch = self.payload if isinstance(self.payload, list) else [self.payload]
            made = []
            for item in batch:
                row = {**db.defaults.get(self.table, {}), **copy.deepcopy(item)}
                row.setdefault("id", str(uuid.uuid4()))
                row.setdefault("created_at", "2026-10-19T08:00:00+00:00")
                row.setdefault("updated_at", row["created_at"])
                db.check_unique(self.table, rows, row, None)
                rows.append(row)
                made.append(copy.deepcopy(row))
            return Result(made)

        matched = [r for r in rows if all(p(r) for p in self.preds)]
        if self.op == "update":
            for r in matched:
                candidate = {**r, **copy.deepcopy(self.payload)}
                db.check_unique(self.table, rows, candidate, r)
                r.update(copy.deepcopy(self.payload))
            return Result([copy.deepcopy(r) for r in matched])
        if self.op == "delete":
            for r in matched:
                db.check_references(self.table, r)
                rows.remove(r)
            return Result([copy.deepcopy(r) for r in matched])

        for col, desc in reversed(self.orders):
            matched.sort(key=lambda r: (r.get(col) is None, str(r.get(col)) if not isinstance(r.get(col), (int, float)) else r.get(col)), reverse=desc)
        total = len(matched)
        if self.span:
            matched = matched[self.span[0]:self.span[1] + 1]
        if self.max_rows is not None:
            matched = matched[:self.max_rows]
        out = [self._project(r) for r in matched]
        if self.single:
            return Result(out[0] if out else None)
        return Result(out, total if self.want_count else None)


class Bucket:
    def __init__(self, db: "FakeDb"):
        self.db = db

    def upload(self, path, content, options=None):
        if "upload" in self.db.fail_storage:
            raise RuntimeError("storage unavailable")
        self.db.files[path] = {"content": content, "options": options or {}}

    def remove(self, paths):
        if "remove" in self.db.fail_storage:
            raise RuntimeError("storage unavailable")
        for p in paths:
            self.db.files.pop(p, None)

    def get_public_url(self, path):
        return f"https://files.test/{path}"

    def download(self, path):
        return self.db.files[path]["content"]


class Storage:
    def __init__(self, db):
        self.db = db

    def from_(self, bucket):
        return Bucket(self.db)


class FakeDb:
    def __init__(self, tables: Optional[Dict[str, List[dict]]] = None):
        self.tables: Dict[str, List[dict]] = copy.deepcopy(tables or {})
        self.files: Dict[str, dict] = {}
        self.calls: List[tuple] = []
        self.fail, self.fail_ops, self.fail_storage = False, set(), set()
        self.missing: Dict[str, set] = {}
        self.defaults = {"documents": {"deleted_at": None, "deleted_by": "", "tags": [], "starred": False, "content_text": "", "text_indexed_at": None}}
        self.storage = Storage(self)

    def table(self, name):
        return Query(self, name)

    # the database's own rules
    def check_unique(self, table: str, rows: List[dict], candidate: dict, current: Optional[dict]) -> None:
        others = [r for r in rows if r is not current]
        zero = "00000000-0000-0000-0000-000000000000"
        if table == "documents" and not candidate.get("deleted_at") and candidate.get("content_hash"):
            for r in others:
                if not r.get("deleted_at") and r.get("content_hash") == candidate["content_hash"] and (r.get("folder_ref") or zero) == (candidate.get("folder_ref") or zero):
                    raise PgError("23505", 'duplicate key value violates unique constraint "documents_one_copy_per_folder"')
        if table == "document_folders":
            for r in others:
                if (r.get("parent_id") or zero) == (candidate.get("parent_id") or zero) and str(r["name"]).lower() == str(candidate["name"]).lower():
                    raise PgError("23505", 'duplicate key value violates unique constraint "document_folders_one_name_per_parent"')
                if candidate.get("seed_key") and r.get("seed_key") == candidate["seed_key"]:
                    raise PgError("23505", 'duplicate key value violates unique constraint "document_folders_seed_key_idx"')

    def check_references(self, table: str, row: dict) -> None:
        if table == "document_folders":
            if any(str(d.get("folder_ref")) == str(row["id"]) for d in self.tables.get("documents", [])) or any(str(f.get("parent_id")) == str(row["id"]) for f in self.tables.get("document_folders", [])):
                raise PgError("23503", 'update or delete on table "document_folders" violates foreign key constraint', "is still referenced from table")


def doc(id: str, name: str = "Report.pdf", **over) -> dict:
    base = {
        "id": id, "name": name, "original_name": name, "storage_path": f"x/{id}.pdf", "file_url": f"https://files.test/x/{id}.pdf",
        "file_size": 1000, "mime_type": "application/pdf", "file_type": "pdf", "ext": "pdf", "folder_ref": None, "folder_path": "",
        "starred": False, "description": "", "tags": [], "content_hash": None, "uploaded_by": "Edson Mavhondo", "content_text": "",
        "search_text": "", "text_indexed_at": "2026-10-19T08:00:00+00:00", "deleted_at": None, "deleted_by": "",
        "created_at": "2026-10-19T08:00:00+00:00", "updated_at": "2026-10-19T08:00:00+00:00", "category_id": "1", "category_name": "x", "folder_id": None,
    }
    base.update(over)
    base["search_text"] = " ".join(str(base.get(k) or "") for k in ("name", "original_name", "description", "folder_path", "ext", "uploaded_by")).lower() + " " + " ".join(base["tags"]).lower()
    return base


def folder(id: str, name: str, parent_id: Optional[str] = None, **over) -> dict:
    base = {"id": id, "parent_id": parent_id, "name": name, "sort_order": 1000, "seed_key": None, "description": "", "created_at": "2026-10-19T08:00:00+00:00",
            "updated_at": "2026-10-19T08:00:00+00:00", "created_by": "", "category_id": "", "category_name": ""}
    base.update(over)
    return base
