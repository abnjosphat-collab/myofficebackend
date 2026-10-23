# tests/test_migrations_rerun_safety.py — guardrail: migration files that
# rebuild a live table from scratch must refuse a re-run inside the file
# itself (a DO-block guard ahead of the DROP) and must sit on the migration
# tracker's refuse list, so "did this run?" can never be answered by running
# it again. See F-06 (maintenance_schedules, Oct 2026).

import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# file -> the live table its DROP would destroy
REBUILT_LIVE_TABLES = {
    "supabase_migration_maintenance_schedules.sql": "maintenance_schedules",
}

_GUARD_RE = re.compile(
    r"DO\s+\$\$.*?information_schema\.tables.*?table_name\s*=\s*'([A-Za-z_][\w$]*)'.*?RAISE\s+EXCEPTION",
    re.IGNORECASE | re.DOTALL,
)
_DROP_RE = re.compile(
    r"^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?([A-Za-z_][\w$]*)",
    re.IGNORECASE | re.MULTILINE,
)


def _retired_in_tracker():
    text = (BACKEND_ROOT / "scripts" / "track_migration.py").read_text(encoding="utf-8")
    names = set(re.findall(r'"(supabase_[A-Za-z0-9_]+\.sql)"', text))
    # Only names inside the refuse-list literal count, not docstring mentions.
    block = text.split("RETIRED_DO_NOT_RERUN", 1)[1] if "RETIRED_DO_NOT_RERUN" in text else ""
    return {n for n in names if n in block}


def test_rebuild_files_guard_before_the_drop():
    for filename, table in REBUILT_LIVE_TABLES.items():
        sql = (BACKEND_ROOT / filename).read_text(encoding="utf-8")
        guard = _GUARD_RE.search(sql)
        assert guard is not None, f"{filename}: no DO-block re-run guard found"
        assert guard.group(1).lower() == table, f"{filename}: guard names {guard.group(1)}, expected {table}"
        drops = [m for m in _DROP_RE.finditer(sql) if m.group(1).lower() == table]
        assert drops, f"{filename}: no DROP of {table} found (guard has nothing to protect?)"
        assert guard.start() < drops[0].start(), f"{filename}: guard must come before the DROP"


def test_rebuild_files_are_on_the_tracker_refuse_list():
    retired = _retired_in_tracker()
    for filename in REBUILT_LIVE_TABLES:
        assert filename in retired, f"{filename}: not on track_migration.py's refuse list"


def test_rebuild_files_header_warns_not_to_rerun():
    for filename in REBUILT_LIVE_TABLES:
        head = (BACKEND_ROOT / filename).read_text(encoding="utf-8")[:1500].lower()
        assert "do not re-run" in head or "never re-run" in head, f"{filename}: header must warn against re-running"
