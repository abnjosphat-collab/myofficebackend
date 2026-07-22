-- Server-side usage analytics event log — captures interactions across every user's
-- device/browser (including anonymous, unsigned-in visitors via a client-generated
-- session_id), so the Usage Analyzer can show real cross-user activity instead of only
-- what's in a single browser's localStorage. Run once in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS public.usage_events (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,               -- client-recorded event time (matches the browser's clock)
  type TEXT NOT NULL CHECK (type IN ('module_open', 'page_view', 'search', 'feedback')),
  session_id TEXT NOT NULL,              -- client-generated, persists per browser session; present even when signed in
  user_id TEXT,                          -- Supabase auth user id, NULL for anonymous/unsigned-in visitors
  user_email TEXT,
  href TEXT,
  path TEXT,
  title TEXT,
  query TEXT,
  results INTEGER,
  rating INTEGER,
  feedback_text TEXT,
  dwell_ms INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()  -- server receipt time, for audit/ingest-lag checks
);

CREATE INDEX IF NOT EXISTS idx_usage_events_ts ON public.usage_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_session ON public.usage_events (session_id);
CREATE INDEX IF NOT EXISTS idx_usage_events_user ON public.usage_events (user_id);

-- The FastAPI backend uses the service-role key and bypasses RLS. No policies are
-- defined on purpose: this table is written/read exclusively through the backend
-- (POST/GET /api/usage/events), never directly from the browser with the anon key.
ALTER TABLE public.usage_events ENABLE ROW LEVEL SECURITY;
