-- Projects scope (Batch 1): read a user's section limit.
--
-- Creates get_user_scope_by_id(UUID): the sibling of get_user_role_by_id,
-- returning user_profiles.permissions->>'scope' (NULL when the account is not
-- limited). SECURITY DEFINER + STABLE so the backend's anon key can call it,
-- same as the role lookup. No data is changed by this file, so there is
-- nothing to back up; the guardrails below stop a partial run.
--
-- APPLY BEFORE DEPLOYING the Batch 1 backend: without this function every
-- sign-in fails loud (503) rather than silently treating scoped accounts
-- as unscoped.
--
-- Run in the Supabase SQL editor, top to bottom.

BEGIN;

-- Guardrail 1: the permissions column the function reads must exist.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'user_profiles'
      AND column_name = 'permissions'
  ) THEN
    RAISE EXCEPTION 'user_profiles.permissions is missing — apply supabase_migration_auth.sql first';
  END IF;
END
$$;

CREATE OR REPLACE FUNCTION public.get_user_scope_by_id(user_id UUID)
RETURNS TEXT
LANGUAGE SQL
SECURITY DEFINER
STABLE AS $$
  SELECT permissions->>'scope' FROM public.user_profiles WHERE id = user_id;
$$;

-- Guardrail 2: the function answers (NULL when nobody matches — never an error).
DO $$
DECLARE
  probe TEXT;
BEGIN
  SELECT public.get_user_scope_by_id('00000000-0000-0000-0000-000000000000') INTO probe;
  IF probe IS NOT NULL THEN
    RAISE EXCEPTION 'get_user_scope_by_id returned a scope for a nobody — refusing to continue';
  END IF;
END
$$;

COMMIT;

-- Verify (read-only): function present with the right contract.
SELECT p.proname AS function,
       pg_get_function_identity_arguments(p.oid) AS args,
       l.lanname AS language,
       p.prosecdef AS security_definer
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
WHERE n.nspname = 'public' AND p.proname = 'get_user_scope_by_id';
