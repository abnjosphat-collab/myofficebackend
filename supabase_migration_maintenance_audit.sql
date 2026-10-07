-- supabase_migration_maintenance_audit.sql
--
-- Maintenance workflow, slice 1: audit trail, row version, comments.
-- Plan: docs/plans/maintenance-workflow.md (myofficefrontend), sections M.2.1, M.2.4, M.2.5 and slice 1.
--
-- NOT APPLIED. Run by the owner in the Supabase SQL editor, then:
--   python scripts/track_migration.py --mark-applied supabase_migration_maintenance_audit.sql
--
-- Properties: additive (two new columns on work_orders, three new objects), safe to run twice, existing
-- readers and writers keep working. Rehearsed on a throwaway local PostgreSQL 16 with
-- scripts/test_maintenance_audit_sql.sh, never against Supabase.
--
-- Check first (read-only). The foreign key below assumes work_orders.id is integer or bigint:
--   select data_type from information_schema.columns where table_name='work_orders' and column_name='id';
--
-- Order of rollout: this file first, then the backend, then the frontend. The backend tolerates the file not
-- being applied yet (no version check, audit appends logged as failures) but the Audit and Comments tabs
-- will show a failure until it is.

-- Row version, bumped by the database on every UPDATE, so any writer (including the old PATCH path)
-- moves it and a stale edit can be detected with: UPDATE ... WHERE id = ? AND version = ?.
CREATE OR REPLACE FUNCTION public.bump_row_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.version := COALESCE(OLD.version, 0) + 1;
  RETURN NEW;
END $$;

ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
DROP TRIGGER IF EXISTS work_orders_bump_version ON public.work_orders;
CREATE TRIGGER work_orders_bump_version BEFORE UPDATE ON public.work_orders
  FOR EACH ROW EXECUTE FUNCTION public.bump_row_version();

-- Client retry token: a retried create (slow or lost network) returns the row it already made.
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS client_token text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_work_orders_client_token
  ON public.work_orders (client_token) WHERE client_token IS NOT NULL;

-- One audit trail for work orders, requests, schedules and assignments. Append-only.
-- entity_id has no foreign key on purpose: the trail must outlive a deleted work order, so the
-- number is copied into entity_number.
CREATE TABLE IF NOT EXISTS public.maintenance_events (
  id            bigserial   PRIMARY KEY,
  entity        text        NOT NULL CHECK (entity IN ('work_order','request','schedule','assignment')),
  entity_id     bigint      NOT NULL,
  entity_number text,
  action        text        NOT NULL,                       -- created, updated, transition, approved, rejected, assigned, ...
  from_status   text,
  to_status     text,
  changes       jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(changes) = 'object'),   -- {field: [old, new]}
  note          text,
  signature     text,                                       -- data URL, kept only on signed approvals and sign-offs
  actor_user_id uuid,
  actor_name    text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_maintenance_events_entity ON public.maintenance_events (entity, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_maintenance_events_actor  ON public.maintenance_events (actor_user_id, created_at DESC);

CREATE OR REPLACE FUNCTION public.maintenance_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'maintenance_events is append-only';
END $$;
DROP TRIGGER IF EXISTS maintenance_events_no_change ON public.maintenance_events;
CREATE TRIGGER maintenance_events_no_change BEFORE UPDATE OR DELETE ON public.maintenance_events
  FOR EACH ROW EXECUTE FUNCTION public.maintenance_events_append_only();

ALTER TABLE public.maintenance_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Authenticated read maintenance events" ON public.maintenance_events;
CREATE POLICY "Authenticated read maintenance events"
  ON public.maintenance_events FOR SELECT USING (auth.role() = 'authenticated');
-- No write policy: only the FastAPI backend (service role, bypasses RLS) appends.

-- Comments on a work order (the Comments tab).
CREATE TABLE IF NOT EXISTS public.work_order_comments (
  id            bigserial   PRIMARY KEY,
  work_order_id bigint      NOT NULL REFERENCES public.work_orders(id) ON DELETE CASCADE,
  body          text        NOT NULL CHECK (length(btrim(body)) > 0),
  author_user_id uuid,
  author_name   text        NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_work_order_comments_wo ON public.work_order_comments (work_order_id, created_at);
ALTER TABLE public.work_order_comments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Authenticated read work order comments" ON public.work_order_comments;
CREATE POLICY "Authenticated read work order comments"
  ON public.work_order_comments FOR SELECT USING (auth.role() = 'authenticated');


NOTIFY pgrst, 'reload schema';

-- Read-only checks to run afterwards:
--   select column_name from information_schema.columns where table_name='work_orders' and column_name in ('version','client_token');   -- 2 rows
--   select count(*) from public.maintenance_events;            -- 0
--   select count(*) from public.work_order_comments;           -- 0
--   select count(*) from public.work_orders;                   -- unchanged from before

-- ROLLBACK (last resort; loses the audit rows and comments entered since). Commented on purpose.
-- DROP TRIGGER IF EXISTS work_orders_bump_version ON public.work_orders;   -- before the function and the column
-- DROP TABLE IF EXISTS public.work_order_comments;
-- DROP TABLE IF EXISTS public.maintenance_events;
-- DROP FUNCTION IF EXISTS public.maintenance_events_append_only();
-- DROP INDEX IF EXISTS public.uq_work_orders_client_token;
-- ALTER TABLE public.work_orders DROP COLUMN IF EXISTS client_token, DROP COLUMN IF EXISTS version;
-- DROP FUNCTION IF EXISTS public.bump_row_version();
-- DELETE FROM schema_migrations WHERE filename = 'supabase_migration_maintenance_audit.sql';
