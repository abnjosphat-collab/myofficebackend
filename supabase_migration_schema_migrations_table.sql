-- Tracking table for backend/scripts/track_migration.py. Records which of the
-- supabase_migration_*.sql files have actually been run against this database —
-- previously there was no tracking at all ("a human runs it and remembers").
-- This table does NOT execute migrations; a human still reviews and runs each
-- .sql file manually in the Supabase SQL editor, then records it here via
-- `python scripts/track_migration.py --mark-applied <filename>`.
-- Run once in the Supabase SQL editor. Safe to re-run (IF NOT EXISTS is idempotent).

CREATE TABLE IF NOT EXISTS schema_migrations (
  id SERIAL PRIMARY KEY,
  filename TEXT NOT NULL UNIQUE,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  note TEXT
);
