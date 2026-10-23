"""
Migration tracking — NOT an executor.

backend/README.md used to say plainly: "there's no migration runner", and there
still isn't one here. This codebase's `supabase` client is REST/PostgREST-based
(app/supabase_client.py) with no direct Postgres connection configured anywhere
(no DATABASE_URL, no psycopg2) — it genuinely cannot run arbitrary DDL from a
.sql file. Production schema changes are still reviewed and run by hand in the
Supabase SQL editor, deliberately — that's appropriate for DDL against the one
production database this project has.

What this script actually fixes: there was no record of which of the
supabase_migration_*.sql files at the repo root had actually been run — "a
human runs it and remembers" was the entire mechanism. This gives real
applied/pending visibility instead, backed by the schema_migrations table
(see supabase_migration_schema_migrations_table.sql — run that one first).

Usage:
    python scripts/track_migration.py --list
    python scripts/track_migration.py --mark-applied supabase_migration_foo.sql
    python scripts/track_migration.py --mark-applied supabase_migration_foo.sql --note "ran against prod 2026-08-28"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.supabase_client import supabase  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that ran once and must never be re-applied: re-running them destroys
# live data. --list shows them as retired (never pending); --mark-applied
# refuses them, because the only reason to touch them now is a re-run.
RETIRED_DO_NOT_RERUN = {
    "supabase_migration_maintenance_schedules.sql": (
        "applied once; re-running drops every live maintenance schedule "
        "(guarded in-file since Oct 2026, but never run it again)"
    ),
}


def migration_files() -> list[str]:
    # supabase_rollback_* is deliberately excluded: emergency-only scripts must
    # never show up as pending-to-apply.
    files: set[str] = set()
    for pattern in ("supabase_migration_*.sql", "supabase_fix_*.sql", "supabase_cleanup_*.sql"):
        files.update(p.name for p in REPO_ROOT.glob(pattern))
    return sorted(files)


def applied_map() -> dict:
    try:
        rows = supabase.table("schema_migrations").select("filename, applied_at, note").execute().data or []
    except Exception as e:
        print(f"Could not read schema_migrations — has supabase_migration_schema_migrations_table.sql been run yet? ({e})")
        sys.exit(1)
    return {r["filename"]: r for r in rows}


def cmd_list():
    applied = applied_map()
    files = migration_files()
    if not files:
        print("No supabase_migration_*.sql files found at the repo root.")
        return
    pending = 0
    retired = 0
    for f in files:
        if f in RETIRED_DO_NOT_RERUN:
            retired += 1
            print(f"  [RETIRED — never re-run] {f} ({RETIRED_DO_NOT_RERUN[f]})")
            continue
        row = applied.get(f)
        if row:
            print(f"  [applied {row['applied_at']}] {f}" + (f" — {row['note']}" if row.get("note") else ""))
        else:
            pending += 1
            print(f"  [PENDING]            {f}")
    # Tracking rows referencing a file that's since been deleted/renamed — surface it
    # rather than silently dropping it, in case it's a rename that needs reconciling.
    orphaned = set(applied) - set(files)
    for f in sorted(orphaned):
        print(f"  [applied, file missing] {f} — tracked as applied but no longer exists in the repo")
    print(f"\n{len(files) - pending - retired}/{len(files)} tracked as applied, {pending} pending, {retired} retired.")


def cmd_mark_applied(filename: str, note: str | None):
    if filename in RETIRED_DO_NOT_RERUN:
        print(f"Refusing to mark {filename}: retired, never re-run ({RETIRED_DO_NOT_RERUN[filename]})")
        sys.exit(1)
    if not (REPO_ROOT / filename).exists():
        print(f"Warning: {filename} doesn't exist at the repo root — marking it applied anyway (could be a typo, or the file was since removed).")
    try:
        supabase.table("schema_migrations").upsert(
            {"filename": filename, "note": note}, on_conflict="filename"
        ).execute()
    except Exception as e:
        print(f"Failed to record {filename}: {e}")
        sys.exit(1)
    print(f"Recorded {filename} as applied.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="Show applied/pending status for every migration file")
    parser.add_argument("--mark-applied", metavar="FILENAME", help="Record a migration file as applied (after running it manually)")
    parser.add_argument("--note", default=None, help="Optional note to attach when using --mark-applied")
    args = parser.parse_args()

    if args.mark_applied:
        cmd_mark_applied(args.mark_applied, args.note)
    elif args.list:
        cmd_list()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
