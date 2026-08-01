-- Extends tasks_events with an optional due date + responsible person, and adds
-- an append-only progress-comments log per task. Run once in the Supabase SQL
-- editor (after supabase_migration_tasks_events.sql).

ALTER TABLE public.tasks_events ADD COLUMN IF NOT EXISTS due_date DATE;
ALTER TABLE public.tasks_events ADD COLUMN IF NOT EXISTS responsible_person TEXT;

CREATE TABLE IF NOT EXISTS public.tasks_events_comments (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES public.tasks_events(id) ON DELETE CASCADE,
  author TEXT,
  text TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_events_comments_task ON public.tasks_events_comments (task_id);

-- Backend-only access via service_role, same as tasks_events itself.
ALTER TABLE public.tasks_events_comments ENABLE ROW LEVEL SECURITY;
