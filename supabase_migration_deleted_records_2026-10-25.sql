-- Recently deleted: a full copy of every deleted row (signatures included) with who
-- deleted it and when, so managers can put a record back exactly. Written by
-- app/audit.py for deletes on every table except audit_log, deleted_records and
-- the documents tables (documents keep their own trash). Copies are kept forever:
-- restoring one stamps restored_at/restored_by, never removes it. Run once in the
-- Supabase SQL editor, then check /api/health says `audit: ok`.
--
-- Rollback: DROP TABLE public.deleted_records;

CREATE TABLE IF NOT EXISTS public.deleted_records (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  deleted_at       timestamptz NOT NULL DEFAULT now(),
  table_name       text NOT NULL,
  record_id        text,
  snapshot         jsonb NOT NULL,          -- the whole row, untruncated
  deleted_by_id    text,
  deleted_by_email text NOT NULL DEFAULT 'system',
  deleted_by_name  text,
  restored_at      timestamptz,             -- set when the copy is put back; the row stays
  restored_by      text
);
CREATE INDEX IF NOT EXISTS deleted_records_table_idx ON public.deleted_records (table_name, deleted_at DESC);
CREATE INDEX IF NOT EXISTS deleted_records_at_idx ON public.deleted_records (deleted_at DESC);
ALTER TABLE public.deleted_records ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.deleted_records FROM anon, authenticated, PUBLIC;
GRANT ALL ON TABLE public.deleted_records TO service_role;
