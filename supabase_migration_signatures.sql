-- backend/supabase_migration_signatures.sql
-- Stored signatures: one saved signature per user, reusable at sign-off after
-- a password re-check.
--
-- NOTE: this table holds the user's REUSABLE signature only. The signature
-- captured on each approval is copied into that record's own column
-- (e.g. overtime.approval_signature) at signing time and must never be turned
-- into a reference to this table — otherwise updating your saved signature
-- would retroactively rewrite every approval you have ever made.

-- 1. Table
CREATE TABLE IF NOT EXISTS public.user_signatures (
  user_id    UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  image_data TEXT        NOT NULL,
  source     TEXT        NOT NULL CHECK (source IN ('drawn', 'scanned')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. RLS — a signature is readable and writable only by its owner.
-- The FastAPI backend uses the service-role key and bypasses this; these
-- policies are what protect the table if it is ever queried from the browser
-- with the anon key.
ALTER TABLE public.user_signatures ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users read own signature" ON public.user_signatures;
CREATE POLICY "Users read own signature"
  ON public.user_signatures FOR SELECT
  USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users insert own signature" ON public.user_signatures;
CREATE POLICY "Users insert own signature"
  ON public.user_signatures FOR INSERT
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users update own signature" ON public.user_signatures;
CREATE POLICY "Users update own signature"
  ON public.user_signatures FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users delete own signature" ON public.user_signatures;
CREATE POLICY "Users delete own signature"
  ON public.user_signatures FOR DELETE
  USING (auth.uid() = user_id);

-- 3. updated_at maintenance (set_updated_at() is created in
--    supabase_migration_auth.sql)
DROP TRIGGER IF EXISTS set_user_signatures_updated_at ON public.user_signatures;
CREATE TRIGGER set_user_signatures_updated_at
  BEFORE UPDATE ON public.user_signatures
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

SELECT 'user_signatures table created.' AS result;
