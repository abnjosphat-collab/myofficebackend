-- backend/supabase_migration_maintenance_schedules.sql
-- Recurring maintenance schedules + automatic work-order generation.
--
-- Replaces the previous maintenance_schedules table, which modelled a one-off
-- job (equipment_id + a single scheduled_date) and could not store what the
-- maintenance page actually does: a recurring template that generates work
-- orders. That table was empty and no code called its router, so nothing is
-- lost. Schedules previously lived in the browser's localStorage, which meant
-- they were invisible to every other user and to the server.

DROP TABLE IF EXISTS public.maintenance_schedules CASCADE;

-- 1. The schedule template
CREATE TABLE public.maintenance_schedules (
  id                  BIGSERIAL   PRIMARY KEY,
  name                TEXT        NOT NULL,

  -- Work-order fields copied onto each generated work order. equipment_info is
  -- free text to match work_orders.equipment_info, which the maintenance page
  -- already writes.
  equipment_info      TEXT        NOT NULL DEFAULT '',
  to_department       TEXT        NOT NULL DEFAULT '',
  allocated_to        TEXT        NOT NULL DEFAULT '',
  authorising_foreman TEXT        NOT NULL DEFAULT '',
  estimated_hours     TEXT        NOT NULL DEFAULT '',
  job_request_details TEXT        NOT NULL DEFAULT '',
  job_instructions    TEXT        NOT NULL DEFAULT '',
  priority            TEXT        NOT NULL DEFAULT 'medium'
                                  CHECK (priority IN ('low','medium','high','urgent')),

  -- Recurrence rule. Which of the *_dow / *_dom / *_months / specific_dates
  -- columns apply depends on recurrence_type; the rest are ignored.
  recurrence_type     TEXT        NOT NULL
                                  CHECK (recurrence_type IN
                                    ('daily','weekly','biweekly','monthly','quarterly','yearly','custom')),
  recurrence_dow      SMALLINT    NOT NULL DEFAULT 1 CHECK (recurrence_dow BETWEEN 0 AND 6),
  recurrence_dom      SMALLINT    NOT NULL DEFAULT 1 CHECK (recurrence_dom BETWEEN 1 AND 31),
  recurrence_months   SMALLINT[]  NOT NULL DEFAULT '{}',
  specific_dates      DATE[]      NOT NULL DEFAULT '{}',

  -- Raise the work order this many days before it falls due.
  advance_days        INTEGER     NOT NULL DEFAULT 0 CHECK (advance_days >= 0),
  active              BOOLEAN     NOT NULL DEFAULT TRUE,

  next_due_date       DATE,
  last_generated      TIMESTAMPTZ,

  created_by          UUID        REFERENCES auth.users(id) ON DELETE SET NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX maintenance_schedules_due_idx
  ON public.maintenance_schedules (active, next_due_date);

-- 2. Generation log.
-- The unique constraint is what makes generation idempotent: the cron can run
-- twice, or two workers can race, and a given schedule still produces at most
-- one work order per due date. Without this, a retried run silently
-- double-raises jobs.
CREATE TABLE public.maintenance_schedule_runs (
  id            BIGSERIAL   PRIMARY KEY,
  schedule_id   BIGINT      NOT NULL REFERENCES public.maintenance_schedules(id) ON DELETE CASCADE,
  due_date      DATE        NOT NULL,
  work_order_id BIGINT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (schedule_id, due_date)
);

-- 3. RLS. The FastAPI backend uses the service-role key and bypasses these;
-- they protect the tables if ever queried from the browser with the anon key.
ALTER TABLE public.maintenance_schedules     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.maintenance_schedule_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated read schedules" ON public.maintenance_schedules;
CREATE POLICY "Authenticated read schedules"
  ON public.maintenance_schedules FOR SELECT
  USING (auth.role() = 'authenticated');

DROP POLICY IF EXISTS "Managers write schedules" ON public.maintenance_schedules;
CREATE POLICY "Managers write schedules"
  ON public.maintenance_schedules FOR ALL
  USING (public.my_role() IN ('manager','admin','super_admin'))
  WITH CHECK (public.my_role() IN ('manager','admin','super_admin'));

DROP POLICY IF EXISTS "Authenticated read schedule runs" ON public.maintenance_schedule_runs;
CREATE POLICY "Authenticated read schedule runs"
  ON public.maintenance_schedule_runs FOR SELECT
  USING (auth.role() = 'authenticated');

-- 4. updated_at maintenance (set_updated_at() comes from supabase_migration_auth.sql)
DROP TRIGGER IF EXISTS set_maintenance_schedules_updated_at ON public.maintenance_schedules;
CREATE TRIGGER set_maintenance_schedules_updated_at
  BEFORE UPDATE ON public.maintenance_schedules
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

SELECT 'maintenance_schedules rebuilt for recurring schedules + generation log.' AS result;
