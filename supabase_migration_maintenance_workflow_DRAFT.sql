-- supabase_migration_maintenance_workflow_DRAFT.sql
--
-- *** DRAFT. NOT APPLIED. DO NOT RUN. ***
-- Written in Phase 0 (analysis and design) of the Maintenance workflow rebuild. It has not been run
-- against Supabase, and nothing in the backend code depends on it yet. The plan it implements is
-- docs/plans/maintenance-workflow.md in the myofficefrontend repository (section M, model).
--
-- How this file will be used:
--   1. The owner reviews it with the plan. Open questions in the plan (section Q) may change it.
--   2. In Phase 2 it is split into one file per delivery slice (the section markers below show where),
--      each named supabase_migration_maintenance_<slice>.sql, so each can be reviewed, run by the owner
--      in the Supabase SQL editor, and recorded with scripts/track_migration.py --mark-applied.
--   3. Nothing is applied without the owner's say-so.
--
-- Properties every section keeps (checked by reading, and by running against a throwaway local
-- PostgreSQL 16 with stub tables, never against Supabase):
--   * Additive: new nullable or defaulted columns, new tables, new indexes. The only non-additive step
--     is swapping one unique constraint in section 7 (the new one is created first; see its note).
--   * Safe to run twice (IF NOT EXISTS, CREATE OR REPLACE, DROP ... IF EXISTS before CREATE TRIGGER).
--   * Existing readers keep working: no column is renamed, retyped or dropped; work_orders.status keeps
--     its current values; equipment_info, allocated_to and the sign-off columns stay.
--   * Reversible: the rollback block at the end undoes every section.
--
-- Preconditions to check first (read-only, run these and read the answers before anything else):
--   select data_type from information_schema.columns where table_name='work_orders' and column_name='id';
--   select data_type from information_schema.columns where table_name='equipment'   and column_name='id';
--   select data_type from information_schema.columns where table_name='employees'   and column_name='id';
--   select data_type from information_schema.columns where table_name='leaves'      and column_name='employee_id';
--   select status, count(*) from public.work_orders group by status order by 2 desc;   -- section 3 constraint
-- The foreign keys below assume work_orders.id, equipment.id and employees.id are integer or bigint. The
-- base DDL of work_orders, equipment, employees and leaves is NOT in this repository, so their column
-- types are assumptions until the queries above are read.

-- =====================================================================================================
-- SECTION 1 (slice 1: audit and concurrency)  R12, R13, R37
-- =====================================================================================================

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

-- =====================================================================================================
-- SECTION 2 (slice 2: links to the existing registers, and the leave lookup)  R7, R8, R34
-- =====================================================================================================
-- The text columns stay (equipment_info, allocated_to, authorising_foreman): they are the display value
-- and the free-text provision for something not on a register. The id columns are filled when the user
-- picked a register row. An id column is NULL for free text.
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS equipment_id          bigint REFERENCES public.equipment(id) ON DELETE SET NULL;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS allocated_employee_id bigint REFERENCES public.employees(id) ON DELETE SET NULL;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS foreman_employee_id   bigint REFERENCES public.employees(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_work_orders_equipment_id ON public.work_orders (equipment_id);
CREATE INDEX IF NOT EXISTS idx_work_orders_allocated_employee ON public.work_orders (allocated_employee_id);

-- Leave availability reads the existing leaves table; it adds no table. This index serves the one
-- question asked on every assignment: "is this person on approved leave on this day?"
CREATE INDEX IF NOT EXISTS idx_leaves_approved_window
  ON public.leaves (employee_id, start_date, end_date) WHERE status = 'approved';

-- =====================================================================================================
-- SECTION 3 (slices 3 and 4: lifecycle, feedback, permits, breakdown link)  R4 to R6, R9 to R11
-- =====================================================================================================
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS scheduled_date date;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS started_at   timestamptz;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS completed_at timestamptz;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS repair_hours numeric(8,2);
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS requester_feedback    text;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS requester_feedback_by uuid;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS requester_feedback_at timestamptz;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS needs_assignment boolean NOT NULL DEFAULT false;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS artisan_signed_by uuid;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS artisan_signed_at timestamptz;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS foreman_signed_by uuid;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS foreman_signed_at timestamptz;
-- Link to the breakdown (downtime) record. breakdowns.id is addressed as a string in app/routers/breakdowns.py,
-- so this is text with no foreign key until its real type is confirmed (precondition query above).
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS breakdown_id text;
-- Permit checklist: {"permit_to_work": {"required": true, "reference": "PTW-123"}, "hot_work": {...}, ...,
-- "other": {"required": true, "reference": "", "label": "Working at height"}}. The keys are fixed by the
-- backend; the database only insists it is an object.
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS permits jsonb NOT NULL DEFAULT '{}'::jsonb;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'work_orders_permits_object') THEN
    ALTER TABLE public.work_orders ADD CONSTRAINT work_orders_permits_object CHECK (jsonb_typeof(permits) = 'object');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'work_orders_repair_hours_nonneg') THEN
    ALTER TABLE public.work_orders ADD CONSTRAINT work_orders_repair_hours_nonneg CHECK (repair_hours IS NULL OR repair_hours >= 0);
  END IF;
  -- NOT VALID: checks new and changed rows only, so existing rows holding an unexpected status do not
  -- block the migration. Run the status query in the preconditions, fix any strays, then (optionally)
  -- VALIDATE CONSTRAINT work_orders_status_check.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'work_orders_status_check') THEN
    ALTER TABLE public.work_orders ADD CONSTRAINT work_orders_status_check
      CHECK (status IN ('pending','in-progress','on-hold','postponed','completed','cancelled','not-done')) NOT VALID;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_work_orders_status_due   ON public.work_orders (status, due_date);
CREATE INDEX IF NOT EXISTS idx_work_orders_scheduled    ON public.work_orders (scheduled_date);
CREATE INDEX IF NOT EXISTS idx_work_orders_breakdown_id ON public.work_orders (breakdown_id) WHERE breakdown_id IS NOT NULL;

-- One atomic step for a lifecycle change: re-check version and status under a row lock, change the
-- status and its timestamps, record the sign-off, and append the audit event, all or nothing. The
-- backend decides WHETHER the move is allowed (one transition table, in Python) and who may do it; this
-- function guarantees it is applied once, to the row the caller saw.
CREATE OR REPLACE FUNCTION public.maintenance_apply_transition(
  p_id               bigint,
  p_expected_version integer,
  p_from             text,
  p_to               text,
  p_set              jsonb,      -- optional: artisan_sign, foreman_sign, foreman_name, notes, progress
  p_actor            uuid,
  p_actor_name       text,
  p_note             text,
  p_signature        text
) RETURNS public.work_orders
LANGUAGE plpgsql AS $$
DECLARE
  v_row public.work_orders;
BEGIN
  SELECT * INTO v_row FROM public.work_orders WHERE id = p_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'WORK_ORDER_NOT_FOUND' USING ERRCODE = 'P0002'; END IF;
  IF v_row.version <> p_expected_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE = '40001'; END IF;
  IF v_row.status <> p_from THEN RAISE EXCEPTION 'STATUS_CONFLICT' USING ERRCODE = '40001'; END IF;

  UPDATE public.work_orders SET
    status       = p_to,
    started_at   = CASE WHEN p_to = 'in-progress' THEN COALESCE(started_at, now()) ELSE started_at END,
    completed_at = CASE WHEN p_to = 'completed'   THEN COALESCE(completed_at, now()) ELSE completed_at END,
    artisan_sign      = CASE WHEN p_set ? 'artisan_sign' THEN p_set->>'artisan_sign' ELSE artisan_sign END,
    artisan_signed_by = CASE WHEN p_set ? 'artisan_sign' THEN p_actor ELSE artisan_signed_by END,
    artisan_signed_at = CASE WHEN p_set ? 'artisan_sign' THEN now()   ELSE artisan_signed_at END,
    foreman_sign      = CASE WHEN p_set ? 'foreman_sign' THEN p_set->>'foreman_sign' ELSE foreman_sign END,
    foreman_name      = CASE WHEN p_set ? 'foreman_name' THEN p_set->>'foreman_name' ELSE foreman_name END,
    foreman_signed_by = CASE WHEN p_set ? 'foreman_sign' THEN p_actor ELSE foreman_signed_by END,
    foreman_signed_at = CASE WHEN p_set ? 'foreman_sign' THEN now()   ELSE foreman_signed_at END,
    notes             = CASE WHEN p_set ? 'notes' THEN p_set->>'notes' ELSE notes END,
    updated_at   = now()
  WHERE id = p_id
  RETURNING * INTO v_row;

  INSERT INTO public.maintenance_events
    (entity, entity_id, entity_number, action, from_status, to_status, changes, note, signature, actor_user_id, actor_name)
  VALUES
    ('work_order', p_id, v_row.work_order_number, 'transition', p_from, p_to, COALESCE(p_set - 'artisan_sign' - 'foreman_sign', '{}'::jsonb),
     p_note, p_signature, p_actor, p_actor_name);
  RETURN v_row;
END $$;

-- =====================================================================================================
-- SECTION 4 (slice 6: work order requests)  R15 to R20
-- =====================================================================================================
CREATE SEQUENCE IF NOT EXISTS public.maintenance_request_seq;

CREATE TABLE IF NOT EXISTS public.maintenance_requests (
  id                    bigserial   PRIMARY KEY,
  request_number        text        NOT NULL DEFAULT ('REQ-' || lpad(nextval('public.maintenance_request_seq')::text, 5, '0')),
  client_token          text,
  status                text        NOT NULL DEFAULT 'open' CHECK (status IN ('open','approved','rejected','cancelled')),
  requester_user_id     uuid,
  requester_employee_id bigint      REFERENCES public.employees(id) ON DELETE SET NULL,
  requester_name        text        NOT NULL DEFAULT '',
  from_department       text        NOT NULL DEFAULT '',
  from_section          text        NOT NULL DEFAULT '',
  equipment_id          bigint      REFERENCES public.equipment(id) ON DELETE SET NULL,
  equipment_text        text        NOT NULL DEFAULT '',
  description           text        NOT NULL CHECK (length(btrim(description)) > 0),
  classification        text        CHECK (classification IS NULL OR classification IN ('planned_maintenance','project','breakdown','custom')),
  priority              text        NOT NULL DEFAULT 'medium' CHECK (priority IN ('low','medium','high','urgent')),
  needed_by             date,
  foreman_employee_id   bigint      REFERENCES public.employees(id) ON DELETE SET NULL,
  foreman_text          text        NOT NULL DEFAULT '',
  decided_by_user_id    uuid,
  decided_by_name       text,
  decided_at            timestamptz,
  decision_note         text,
  version               integer     NOT NULL DEFAULT 1,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT maintenance_requests_equipment_given CHECK (equipment_id IS NOT NULL OR length(btrim(equipment_text)) > 0),
  CONSTRAINT maintenance_requests_decision_recorded CHECK (status NOT IN ('approved','rejected') OR (decided_at IS NOT NULL AND decided_by_user_id IS NOT NULL)),
  CONSTRAINT maintenance_requests_rejection_reason CHECK (status <> 'rejected' OR length(btrim(COALESCE(decision_note, ''))) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_maintenance_requests_number ON public.maintenance_requests (request_number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_maintenance_requests_token  ON public.maintenance_requests (client_token) WHERE client_token IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_maintenance_requests_status    ON public.maintenance_requests (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_maintenance_requests_requester ON public.maintenance_requests (requester_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_maintenance_requests_foreman   ON public.maintenance_requests (foreman_employee_id) WHERE status = 'open';

DROP TRIGGER IF EXISTS maintenance_requests_bump_version ON public.maintenance_requests;
CREATE TRIGGER maintenance_requests_bump_version BEFORE UPDATE ON public.maintenance_requests
  FOR EACH ROW EXECUTE FUNCTION public.bump_row_version();
DROP TRIGGER IF EXISTS set_maintenance_requests_updated_at ON public.maintenance_requests;
CREATE TRIGGER set_maintenance_requests_updated_at BEFORE UPDATE ON public.maintenance_requests
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();   -- from supabase_migration_auth.sql

-- The single truth for "which work order did this request become". Unique, so a request can become at
-- most one work order however many times approval is retried.
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS request_id bigint REFERENCES public.maintenance_requests(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_work_orders_request_id ON public.work_orders (request_id) WHERE request_id IS NOT NULL;

ALTER TABLE public.maintenance_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Requester or manager reads requests" ON public.maintenance_requests;
CREATE POLICY "Requester or manager reads requests"
  ON public.maintenance_requests FOR SELECT
  USING (requester_user_id = auth.uid() OR public.my_role() IN ('manager','admin','super_admin'));

-- Approve a request and make its work order in one transaction: lock the request, make sure it is still
-- open and at the version the approver saw, insert the work order, mark the request approved, append
-- the audit events. A retry after success returns the work order already made (idempotent).
-- p_wo carries the values the backend decided for the work order, including the work order number
-- (the backend allocates it and retries on the unique-number error, as it does today).
CREATE OR REPLACE FUNCTION public.maintenance_approve_request(
  p_request_id       bigint,
  p_expected_version integer,
  p_actor            uuid,
  p_actor_name       text,
  p_signature        text,
  p_note             text,
  p_wo               jsonb
) RETURNS public.work_orders
LANGUAGE plpgsql AS $$
DECLARE
  v_req public.maintenance_requests;
  v_wo  public.work_orders;
BEGIN
  SELECT * INTO v_req FROM public.maintenance_requests WHERE id = p_request_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'REQUEST_NOT_FOUND' USING ERRCODE = 'P0002'; END IF;

  IF v_req.status = 'approved' THEN
    SELECT * INTO v_wo FROM public.work_orders WHERE request_id = p_request_id;
    IF FOUND THEN RETURN v_wo; END IF;
  END IF;
  IF v_req.status <> 'open' THEN RAISE EXCEPTION 'STATUS_CONFLICT' USING ERRCODE = '40001'; END IF;
  IF v_req.version <> p_expected_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE = '40001'; END IF;

  INSERT INTO public.work_orders (
    work_order_number, request_id, title, description, department, equipment, equipment_info, equipment_id,
    to_department, to_section, from_department, from_section, allocated_to, allocated_employee_id,
    authorising_foreman, foreman_employee_id, job_request_details, job_instructions, priority, status,
    classification, date_raised, time_raised, scheduled_date, due_date, requested_by, notes, permits, needs_assignment
  ) VALUES (
    p_wo->>'work_order_number', p_request_id, p_wo->>'title', p_wo->>'job_request_details', p_wo->>'to_department',
    p_wo->>'equipment_info', p_wo->>'equipment_info', (p_wo->>'equipment_id')::bigint,
    COALESCE(p_wo->>'to_department', ''), COALESCE(p_wo->>'to_section', ''), COALESCE(p_wo->>'from_department', ''), COALESCE(p_wo->>'from_section', ''),
    COALESCE(p_wo->>'allocated_to', ''), (p_wo->>'allocated_employee_id')::bigint,
    COALESCE(p_wo->>'authorising_foreman', ''), (p_wo->>'foreman_employee_id')::bigint,
    p_wo->>'job_request_details', COALESCE(p_wo->>'job_instructions', ''), COALESCE(p_wo->>'priority', 'medium'), 'pending',
    p_wo->>'classification', (p_wo->>'date_raised')::date, p_wo->>'time_raised', (p_wo->>'scheduled_date')::date, (p_wo->>'due_date')::date,
    COALESCE(p_wo->>'requested_by', ''), p_wo->>'notes', COALESCE(p_wo->'permits', '{}'::jsonb), COALESCE((p_wo->>'needs_assignment')::boolean, false)
  ) RETURNING * INTO v_wo;

  UPDATE public.maintenance_requests SET
    status = 'approved', decided_by_user_id = p_actor, decided_by_name = p_actor_name, decided_at = now(), decision_note = p_note
  WHERE id = p_request_id;

  INSERT INTO public.maintenance_events (entity, entity_id, entity_number, action, from_status, to_status, note, signature, actor_user_id, actor_name)
  VALUES ('request', p_request_id, v_req.request_number, 'approved', 'open', 'approved', p_note, p_signature, p_actor, p_actor_name);
  INSERT INTO public.maintenance_events (entity, entity_id, entity_number, action, to_status, note, actor_user_id, actor_name)
  VALUES ('work_order', v_wo.id, v_wo.work_order_number, 'created', 'pending', 'From request ' || v_req.request_number, p_actor, p_actor_name);
  RETURN v_wo;
END $$;

-- =====================================================================================================
-- SECTION 5 (slice 7: assignments)  R7, R8
-- =====================================================================================================
-- Who does the job, on which day. work_orders.allocated_to / allocated_employee_id keep the LEAD for
-- every existing reader; the backend keeps them in step with the lead row here.
CREATE TABLE IF NOT EXISTS public.work_order_assignments (
  id             bigserial   PRIMARY KEY,
  work_order_id  bigint      NOT NULL REFERENCES public.work_orders(id) ON DELETE CASCADE,
  employee_id    bigint      REFERENCES public.employees(id) ON DELETE RESTRICT,
  employee_text  text        NOT NULL DEFAULT '',          -- free-text provision (contractor, someone not on the register)
  role           text        NOT NULL DEFAULT 'lead' CHECK (role IN ('lead','assistant')),
  planned_date   date        NOT NULL,
  planned_hours  numeric(5,2) CHECK (planned_hours IS NULL OR planned_hours > 0),
  actual_hours   numeric(6,2) CHECK (actual_hours IS NULL OR actual_hours >= 0),
  status         text        NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','published','done','removed')),
  published_at   timestamptz,
  assigned_by    uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT work_order_assignments_person CHECK (employee_id IS NOT NULL OR length(btrim(employee_text)) > 0)
);
-- One lead per work order; one row per person per day per work order.
CREATE UNIQUE INDEX IF NOT EXISTS uq_wo_assignments_lead   ON public.work_order_assignments (work_order_id) WHERE role = 'lead' AND status <> 'removed';
CREATE UNIQUE INDEX IF NOT EXISTS uq_wo_assignments_person ON public.work_order_assignments (work_order_id, employee_id, planned_date) WHERE employee_id IS NOT NULL AND status <> 'removed';
CREATE INDEX IF NOT EXISTS idx_wo_assignments_employee_day ON public.work_order_assignments (employee_id, planned_date) WHERE status <> 'removed';
CREATE INDEX IF NOT EXISTS idx_wo_assignments_day          ON public.work_order_assignments (planned_date) WHERE status <> 'removed';
DROP TRIGGER IF EXISTS set_wo_assignments_updated_at ON public.work_order_assignments;
CREATE TRIGGER set_wo_assignments_updated_at BEFORE UPDATE ON public.work_order_assignments
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
ALTER TABLE public.work_order_assignments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Authenticated read assignments" ON public.work_order_assignments;
CREATE POLICY "Authenticated read assignments" ON public.work_order_assignments FOR SELECT USING (auth.role() = 'authenticated');

-- =====================================================================================================
-- SECTION 6 (slice 8: scheduled work)  R21 to R27
-- =====================================================================================================
ALTER TABLE public.maintenance_schedules ADD COLUMN IF NOT EXISTS classification        text;
ALTER TABLE public.maintenance_schedules ADD COLUMN IF NOT EXISTS to_section            text    NOT NULL DEFAULT '';
ALTER TABLE public.maintenance_schedules ADD COLUMN IF NOT EXISTS permits               jsonb   NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.maintenance_schedules ADD COLUMN IF NOT EXISTS allocated_employee_id bigint  REFERENCES public.employees(id) ON DELETE SET NULL;
ALTER TABLE public.maintenance_schedules ADD COLUMN IF NOT EXISTS foreman_employee_id   bigint  REFERENCES public.employees(id) ON DELETE SET NULL;
-- Skip a due date that falls within this many days after the asset's last completed work order from this
-- schedule (defined in the plan, section R, assumption A5; the owner confirms the meaning in Q).
ALTER TABLE public.maintenance_schedules ADD COLUMN IF NOT EXISTS suppress_days integer NOT NULL DEFAULT 0;
ALTER TABLE public.maintenance_schedules ADD COLUMN IF NOT EXISTS version       integer NOT NULL DEFAULT 1;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'maintenance_schedules_suppress_nonneg') THEN
    ALTER TABLE public.maintenance_schedules ADD CONSTRAINT maintenance_schedules_suppress_nonneg CHECK (suppress_days >= 0);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'maintenance_schedules_permits_object') THEN
    ALTER TABLE public.maintenance_schedules ADD CONSTRAINT maintenance_schedules_permits_object CHECK (jsonb_typeof(permits) = 'object');
  END IF;
END $$;
DROP TRIGGER IF EXISTS maintenance_schedules_bump_version ON public.maintenance_schedules;
CREATE TRIGGER maintenance_schedules_bump_version BEFORE UPDATE ON public.maintenance_schedules
  FOR EACH ROW EXECUTE FUNCTION public.bump_row_version();

-- Many assets per schedule. Replaces the comma-separated maintenance_schedules.equipment_info, which stays
-- (read by existing code and kept in step by the backend) until slice 8 is fully rolled out.
CREATE TABLE IF NOT EXISTS public.schedule_assets (
  id             bigserial   PRIMARY KEY,
  schedule_id    bigint      NOT NULL REFERENCES public.maintenance_schedules(id) ON DELETE CASCADE,
  equipment_id   bigint      REFERENCES public.equipment(id) ON DELETE SET NULL,
  equipment_text text        NOT NULL DEFAULT '',
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT schedule_assets_asset_given CHECK (equipment_id IS NOT NULL OR length(btrim(equipment_text)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_assets_equipment ON public.schedule_assets (schedule_id, equipment_id) WHERE equipment_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_assets_text      ON public.schedule_assets (schedule_id, lower(btrim(equipment_text))) WHERE equipment_id IS NULL;
ALTER TABLE public.schedule_assets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Authenticated read schedule assets" ON public.schedule_assets;
CREATE POLICY "Authenticated read schedule assets" ON public.schedule_assets FOR SELECT USING (auth.role() = 'authenticated');

-- Generation becomes one work order per asset per due date. The run log's uniqueness widens from
-- (schedule, due date) to (schedule, due date, asset). The NEW constraint is created BEFORE the old one
-- is dropped, so idempotency is never absent. Existing rows get equipment_key = '' and stay unique.
ALTER TABLE public.maintenance_schedule_runs ADD COLUMN IF NOT EXISTS equipment_key text NOT NULL DEFAULT '';
ALTER TABLE public.maintenance_schedule_runs ADD COLUMN IF NOT EXISTS skipped_reason text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_runs_asset ON public.maintenance_schedule_runs (schedule_id, due_date, equipment_key);
ALTER TABLE public.maintenance_schedule_runs DROP CONSTRAINT IF EXISTS maintenance_schedule_runs_schedule_id_due_date_key;

-- Which schedule a work order came from (the "parent scheduled work order" in eMaint's list).
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS schedule_id bigint REFERENCES public.maintenance_schedules(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_work_orders_schedule_id ON public.work_orders (schedule_id) WHERE schedule_id IS NOT NULL;

-- OPTIONAL DATA STEP, review on its own. Copies each existing schedule's comma-separated machine names into
-- schedule_assets, linking to the equipment register only when exactly one equipment row has that name.
-- Re-runnable: the unique indexes above make a second run add nothing.
INSERT INTO public.schedule_assets (schedule_id, equipment_id, equipment_text)
SELECT s.id, m.id, btrim(part)
FROM public.maintenance_schedules s
CROSS JOIN LATERAL unnest(string_to_array(s.equipment_info, ',')) AS part
LEFT JOIN LATERAL (
  SELECT min(e.id) AS id FROM public.equipment e WHERE lower(e.name) = lower(btrim(part)) HAVING count(*) = 1
) m ON true
WHERE btrim(part) <> ''
ON CONFLICT DO NOTHING;

NOTIFY pgrst, 'reload schema';

-- =====================================================================================================
-- ROLLBACK (run only if a slice must be undone; order is reverse of the sections). Commented on purpose.
-- Dropping the new tables loses what was entered in them; work orders themselves are untouched.
-- =====================================================================================================
-- DROP TRIGGER IF EXISTS work_orders_bump_version ON public.work_orders;            -- must go before bump_row_version() and the version column
-- DROP TRIGGER IF EXISTS maintenance_schedules_bump_version ON public.maintenance_schedules;
-- DROP FUNCTION IF EXISTS public.maintenance_approve_request(bigint, integer, uuid, text, text, text, jsonb);
-- DROP FUNCTION IF EXISTS public.maintenance_apply_transition(bigint, integer, text, text, jsonb, uuid, text, text, text);
-- DROP INDEX IF EXISTS public.uq_schedule_runs_asset;
-- ALTER TABLE public.maintenance_schedule_runs ADD CONSTRAINT maintenance_schedule_runs_schedule_id_due_date_key UNIQUE (schedule_id, due_date);   -- only if no per-asset rows exist
-- ALTER TABLE public.maintenance_schedule_runs DROP COLUMN IF EXISTS equipment_key, DROP COLUMN IF EXISTS skipped_reason;
-- DROP TABLE IF EXISTS public.schedule_assets;
-- DROP TABLE IF EXISTS public.work_order_assignments;
-- DROP TABLE IF EXISTS public.maintenance_requests CASCADE;     -- removes work_orders.request_id's foreign key
-- DROP SEQUENCE IF EXISTS public.maintenance_request_seq;
-- ALTER TABLE public.maintenance_schedules
--   DROP COLUMN IF EXISTS classification, DROP COLUMN IF EXISTS to_section, DROP COLUMN IF EXISTS permits,
--   DROP COLUMN IF EXISTS allocated_employee_id, DROP COLUMN IF EXISTS foreman_employee_id,
--   DROP COLUMN IF EXISTS suppress_days, DROP COLUMN IF EXISTS version;
-- ALTER TABLE public.work_orders
--   DROP CONSTRAINT IF EXISTS work_orders_status_check, DROP CONSTRAINT IF EXISTS work_orders_permits_object, DROP CONSTRAINT IF EXISTS work_orders_repair_hours_nonneg,
--   DROP COLUMN IF EXISTS schedule_id, DROP COLUMN IF EXISTS request_id, DROP COLUMN IF EXISTS permits, DROP COLUMN IF EXISTS breakdown_id,
--   DROP COLUMN IF EXISTS foreman_signed_at, DROP COLUMN IF EXISTS foreman_signed_by, DROP COLUMN IF EXISTS artisan_signed_at, DROP COLUMN IF EXISTS artisan_signed_by,
--   DROP COLUMN IF EXISTS needs_assignment, DROP COLUMN IF EXISTS requester_feedback_at, DROP COLUMN IF EXISTS requester_feedback_by, DROP COLUMN IF EXISTS requester_feedback,
--   DROP COLUMN IF EXISTS repair_hours, DROP COLUMN IF EXISTS completed_at, DROP COLUMN IF EXISTS started_at, DROP COLUMN IF EXISTS scheduled_date,
--   DROP COLUMN IF EXISTS foreman_employee_id, DROP COLUMN IF EXISTS allocated_employee_id, DROP COLUMN IF EXISTS equipment_id,
--   DROP COLUMN IF EXISTS client_token, DROP COLUMN IF EXISTS version;
-- DROP TABLE IF EXISTS public.work_order_comments;
-- DROP TABLE IF EXISTS public.maintenance_events;
-- DROP FUNCTION IF EXISTS public.maintenance_events_append_only();
-- DROP FUNCTION IF EXISTS public.bump_row_version();
-- DELETE FROM schema_migrations WHERE filename LIKE 'supabase_migration_maintenance_%';
