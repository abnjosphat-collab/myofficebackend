-- C7 themes trial: flag the Projects test account for the appearance trial.
-- The colour-theme and darkness switches (and the stored choice) stay dark for
-- everyone whose profile lacks 'appearance_themes' in permissions.features; this
-- file adds the flag to the Projects test account only, per the owner decision in
-- audit/ux-consistency-2026-10-24/PLAN.md batch 7.
-- NOT RUN YET — run in the Supabase SQL editor on the owner's "run it".
--
-- PREVIEW (read-only — run first; expect the one test account):
--   select id, email, permissions->>'scope' as scope, permissions->'features' as features
--   from public.user_profiles
--   where email = 'abcjosphat+projects@gmail.com'
--     and not (coalesce(permissions->'features', '[]'::jsonb) ? 'appearance_themes');

BEGIN;

-- Guardrail: the permissions column the update reads must exist.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'user_profiles'
      AND column_name = 'permissions'
  ) THEN
    RAISE EXCEPTION 'user_profiles.permissions is missing';
  END IF;
END
$$;

-- Guardrail: the test account must exist (never flag the wrong row).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.user_profiles WHERE email = 'abcjosphat+projects@gmail.com') THEN
    RAISE EXCEPTION 'test account abcjosphat+projects@gmail.com not found';
  END IF;
END
$$;

-- Backup: the test account's current permissions (restore with jsonb_set back).
DROP TABLE IF EXISTS public.user_profiles_backup_2026_10_25;
CREATE TABLE public.user_profiles_backup_2026_10_25 AS
SELECT id, email, permissions
FROM public.user_profiles
WHERE email = 'abcjosphat+projects@gmail.com';

UPDATE public.user_profiles
SET permissions = jsonb_set(
  COALESCE(permissions, '{}'::jsonb),
  '{features}',
  COALESCE(permissions->'features', '[]'::jsonb) || '"appearance_themes"'::jsonb
)
WHERE email = 'abcjosphat+projects@gmail.com'
  AND NOT (COALESCE(permissions->'features', '[]'::jsonb) ? 'appearance_themes');

-- Verify: the test account now carries the flag (expect 0 rows).
SELECT id, email, permissions->'features' AS features
FROM public.user_profiles
WHERE email = 'abcjosphat+projects@gmail.com'
  AND NOT (COALESCE(permissions->'features', '[]'::jsonb) ? 'appearance_themes');

COMMIT;
