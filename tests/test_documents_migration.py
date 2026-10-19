# tests/test_documents_migration.py — the upgrade SQL and the Python folder tree describe the same top of the tree, and the
# SQL keeps its promises: idempotent, never deletes, every table keeps RLS on.
import re
from pathlib import Path

from app import document_structure as ds

SQL = (Path(__file__).resolve().parent.parent / "supabase_migration_documents_v2.sql").read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SQL)  # the statements, without the comments


def test_the_sql_sections_are_the_python_sections():
    in_sql = re.findall(r"\((\d), '([^']+)',\s+'([^']+)'\)", CODE)
    in_python = [(str(int(n.sort_order / ds.SORT_STEP)), n.name, n.key) for n in ds.flatten() if n.depth == 1]
    assert [(num, name, key) for num, name, key in in_sql] == in_python


def test_the_sql_root_is_the_python_root():
    assert f"'{ds.ROOT_NAME}'" in CODE and f"'{ds.ROOT_KEY}'" in CODE


def test_the_sql_never_deletes_or_drops_data():
    assert not re.search(r"\bdelete\s+from\b|\btruncate\b|\bdrop\s+table\b|\bdrop\s+column\b", CODE, re.IGNORECASE)


def test_the_sql_can_be_run_again():
    for statement in re.findall(r"create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?\S+", CODE, re.IGNORECASE):
        assert "if not exists" in statement.lower(), statement
    for statement in re.findall(r"add\s+column\s+(?:if\s+not\s+exists\s+)?\S+", CODE, re.IGNORECASE):
        assert "if not exists" in statement.lower(), statement


def test_the_backfill_does_not_make_every_file_look_edited_today():
    assert CODE.lower().index("disable trigger user") < CODE.lower().index("set ext") < CODE.lower().index("enable trigger user")


def test_every_table_it_touches_keeps_row_level_security_on():
    for table in ("documents", "document_folders", "document_activities"):
        assert re.search(rf"alter table public\.{table}\s+enable row level security", CODE, re.IGNORECASE)


def test_the_old_first_migration_no_longer_drops_the_table():
    old = (Path(__file__).resolve().parent.parent / "supabase_migration_documents.sql").read_text(encoding="utf-8")
    assert not re.search(r"^\s*drop\s+table", old, re.IGNORECASE | re.MULTILINE)
