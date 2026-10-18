# tests/test_migrations_rls.py — guardrail: every table created by a
# supabase_migration_*.sql file must have RLS enabled by SOME migration
# file, and the 24 historically exposed tables must stay covered.
#
# Why this exists (2026-10-18, Phase 1 hardening): 24 public tables
# lived with RLS off and anon holding full DML. The fix
# (supabase_migration_enable_rls_public_tables.sql) closes the hole;
# this test stops it reopening — a future migration that adds
# CREATE TABLE without ENABLE ROW LEVEL SECURITY fails here.
#
# Coverage rules (documented; keep them tight):
#   * CREATE TABLE matches qualified (public.x) and unqualified (x)
#     forms — both land in the public schema under the default
#     search_path, so both need RLS.
#   * Coverage is an ALTER TABLE [public.]x ENABLE ROW LEVEL
#     SECURITY in any migration file, OR membership in a
#     data-driven loop (engineering.sql's FOREACH array, the
#     enable-RLS file's to_regclass-guarded block): the table name
#     as a quoted string in a file that also contains to_regclass /
#     FOREACH and ENABLE ROW LEVEL SECURITY.
#   * Matching is comment- and string-aware: commented-out SQL and
#     SQL text inside string literals (e.g. EXECUTE format('ALTER
#     TABLE ...')) must not count as real statements. Dollar-quoted
#     DO/function bodies are handled; backslash escapes inside
#     E'' strings are not (no migration uses them — see
#     test_sql_scanner_ignores_comments_and_strings).

from pathlib import Path
import re

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENABLE_RLS_FILE = "supabase_migration_enable_rls_public_tables.sql"

# The 24 tables found with RLS off + anon DML (2026-10-18 audit).
EXPECTED_RLS_TABLES = [
    "employees", "leaves", "overtime", "duty_roster", "duty_rotations",
    "rotation_covers", "standby_rotations", "standby_schedules", "drivers",
    "password_reset_requests", "work_orders", "breakdowns", "daily_reports",
    "sheq_reports", "sheq_findings", "sheq_inspections", "ppe_records",
    "spare_parts", "spare_requisitions", "stock_issues", "notices",
    "document_categories", "visualizations",
    "timesheets_backup_20261014_holiday",
]

_CREATE_TABLE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?([A-Za-z_][\w$]*)\s*\(",
    re.IGNORECASE,
)
_ENABLE_RLS_RE = re.compile(
    r"alter\s+table\s+(?:if\s+exists\s+)?(?:public\.)?([A-Za-z_][\w$]*)\s+"
    r"enable\s+row\s+level\s+security",
    re.IGNORECASE,
)
_QUOTED_WORD_RE = re.compile(r"'([A-Za-z_][\w$]*)'")
_DOLLAR_TAG_RE = re.compile(r"\$[A-Za-z_][\w$]*\$|\$\$")


def _scan_sql(text):
    """Split SQL into code vs quoted words, ignoring comments.

    Returns (code, quoted_words) where `code` is the input with every
    comment and string-literal character blanked (newlines kept) and
    `quoted_words` is the set of single-quoted identifiers found
    OUTSIDE comments (used for data-driven loop coverage).
    Handles -- line comments, /* */ blocks, '...' strings with ''
    escapes, and $tag$...$tag$ dollar-quoted bodies.
    """
    code_chars = list(text)
    in_line_comment = False
    i, n = 0, len(text)

    def blank(start, end):
        for j in range(start, end):
            if code_chars[j] != "\n":
                code_chars[j] = " "

    while i < n:
        if in_line_comment:
            if text[i] == "\n":
                in_line_comment = False
            i += 1
            continue
        two = text[i:i + 2]
        if two == "--":
            end = text.find("\n", i)
            end = n if end == -1 else end
            blank(i, end)
            i = end
            continue
        if two == "/*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            blank(i, end)
            i = end
            continue
        if text[i] == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if text[j + 1:j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            blank(i, min(j + 1, n))
            i = min(j + 1, n)
            continue
        if text[i] == "$":
            m = _DOLLAR_TAG_RE.match(text, i)
            if m:
                tag = m.group(0)
                end = text.find(tag, m.end())
                end = n if end == -1 else end + len(tag)
                blank(i, end)
                i = end
                continue
        i += 1

    code = "".join(code_chars)
    # Quoted words: re-scan comment-free text. Rebuild comment-free
    # text by blanking only comments (strings intact).
    stripped = []
    i = 0
    while i < n:
        two = text[i:i + 2]
        if two == "--":
            end = text.find("\n", i)
            end = n if end == -1 else end
            stripped.append(" " * (end - i))
            i = end
            continue
        if two == "/*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            chunk = text[i:end].replace("\n", "\n")
            stripped.append("".join(" " if c != "\n" else c for c in chunk))
            i = end
            continue
        # Skip over strings so comment markers inside them survive.
        if text[i] == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if text[j + 1:j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            stripped.append(text[i:min(j + 1, n)])
            i = min(j + 1, n)
            continue
        if text[i] == "$":
            m = _DOLLAR_TAG_RE.match(text, i)
            if m:
                tag = m.group(0)
                end = text.find(tag, m.end())
                end = n if end == -1 else end + len(tag)
                stripped.append(text[i:end])
                i = end
                continue
        stripped.append(text[i])
        i += 1
    no_comments = "".join(stripped)
    return code, set(_QUOTED_WORD_RE.findall(no_comments)), no_comments


def _analyse_migrations():
    """Parse every supabase_migration_*.sql.

    Returns (files, created, covered) where created maps table ->
    defining file and covered is the set of tables with RLS enabled
    in some migration file (explicit ALTER or loop membership).
    """
    files = sorted(BACKEND_ROOT.glob("supabase_migration_*.sql"))
    assert files, f"no supabase_migration_*.sql under {BACKEND_ROOT}"
    created, covered = {}, set()
    for path in files:
        code, quoted, no_comments = _scan_sql(path.read_text(encoding="utf-8"))
        for name in _CREATE_TABLE_RE.findall(code):
            created.setdefault(name.lower(), path.name)
        for name in _ENABLE_RLS_RE.findall(code):
            covered.add(name.lower())
        lowered = no_comments.lower()
        if ("foreach" in lowered or "to_regclass" in lowered) and \
                "enable row level security" in lowered:
            covered.update(w.lower() for w in quoted)
    return files, created, covered


def test_every_created_table_has_rls_enabled_somewhere():
    _, created, covered = _analyse_migrations()
    assert created, "parser found zero CREATE TABLEs — regex broke?"
    missing = {t: f for t, f in sorted(created.items()) if t not in covered}
    assert not missing, (
        "tables created without ENABLE ROW LEVEL SECURITY in any "
        "migration file (add it to the table's own migration): "
        + ", ".join(f"{t} ({f})" for t, f in missing.items())
    )


def test_all_24_hardened_tables_stay_covered():
    _, _, covered = _analyse_migrations()
    missing = [t for t in EXPECTED_RLS_TABLES if t not in covered]
    assert not missing, (
        "hardened tables lost RLS coverage — "
        + ", ".join(missing)
    )


def test_enable_rls_migration_carries_all_24_explicitly():
    path = BACKEND_ROOT / ENABLE_RLS_FILE
    assert path.exists(), f"{ENABLE_RLS_FILE} missing from backend/"
    code, _, _ = _scan_sql(path.read_text(encoding="utf-8"))
    explicit = {n.lower() for n in _ENABLE_RLS_RE.findall(code)}
    missing = [t for t in EXPECTED_RLS_TABLES if t not in explicit]
    assert not missing, f"{ENABLE_RLS_FILE} lacks explicit ALTER for: {missing}"


def test_enable_rls_migration_drops_open_sheq_policies():
    text = (BACKEND_ROOT / ENABLE_RLS_FILE).read_text(encoding="utf-8")
    for table in ("sheq_findings", "sheq_inspections"):
        pattern = re.compile(
            r'drop\s+policy\s+if\s+exists\s+"Enable all for authenticated users"\s+'
            rf"on\s+public\.{table}",
            re.IGNORECASE,
        )
        assert pattern.search(text), (
            f"{ENABLE_RLS_FILE} must DROP POLICY IF EXISTS "
            f'"Enable all for authenticated users" on public.{table}'
        )


def test_sql_scanner_ignores_comments_and_strings():
    sql = """\
-- CREATE TABLE ghost_table (id int);
-- ALTER TABLE ghost_table ENABLE ROW LEVEL SECURITY;
/* CREATE TABLE block_ghost (id int); */
CREATE TABLE real_table (id int);
DO $$ BEGIN
  EXECUTE format('ALTER TABLE public.%s ENABLE ROW LEVEL SECURITY', 'x');
  -- a comment inside a dollar body mentioning real_table
END $$;
SELECT 'ALTER TABLE real_table ENABLE ROW LEVEL SECURITY';
"""
    code, quoted, _ = _scan_sql(sql)
    assert "ghost_table" not in code and "block_ghost" not in code
    assert "mentioning" not in code  # comment inside dollar body blanked
    assert [n.lower() for n in _CREATE_TABLE_RE.findall(code)] == ["real_table"]
    assert _ENABLE_RLS_RE.findall(code) == [], \
        "string-literal ALTER must not count as coverage"
    assert "x" in quoted  # loop-style quoted names stay visible for coverage
