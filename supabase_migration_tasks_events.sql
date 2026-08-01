-- Manager-only "Events & Tasks" board: upcoming events and to-do items in one list,
-- tagged with task_type so progress can be tracked/visualized per type. Run once in
-- the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS public.tasks_events (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT,
  task_type TEXT NOT NULL DEFAULT 'task',
  event_date DATE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
  priority TEXT NOT NULL DEFAULT 'Medium',
  created_by TEXT,
  completed_by TEXT,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_events_type ON public.tasks_events (task_type);
CREATE INDEX IF NOT EXISTS idx_tasks_events_status ON public.tasks_events (status);
CREATE INDEX IF NOT EXISTS idx_tasks_events_date ON public.tasks_events (event_date);

-- The FastAPI backend uses the service-role key and bypasses RLS. No policies are
-- defined on purpose: this table is written/read exclusively through the backend
-- (the whole /api/tasks-events router is manager-role-gated server-side), never
-- directly from the browser with the anon key.
ALTER TABLE public.tasks_events ENABLE ROW LEVEL SECURITY;
