-- Password-reset requests for the admin-assisted flow: a signed-out user asks,
-- the request waits here, the admin's bell picks it up, and the admin's reset
-- marks it done. One row per requester per source; a second request while one
-- is pending is a no-op (the partial unique index enforces it).
-- Safe to re-run (CREATE TABLE IF NOT EXISTS + DROP/CREATE index).
CREATE TABLE IF NOT EXISTS public.password_reset_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source text NOT NULL CHECK (source IN ('myoffice', 'tools')),
  identifier text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done')),
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz
);
-- Backend-only table (service_role bypasses RLS): RLS on, no policies.
ALTER TABLE public.password_reset_requests ENABLE ROW LEVEL SECURITY;
DROP INDEX IF EXISTS public.password_reset_requests_one_pending;
CREATE UNIQUE INDEX password_reset_requests_one_pending
  ON public.password_reset_requests (source, lower(identifier))
  WHERE status = 'pending';
