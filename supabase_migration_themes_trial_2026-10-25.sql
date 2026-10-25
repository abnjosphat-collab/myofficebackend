-- C7 themes trial: flag the Projects-scoped accounts for the appearance trial.
-- The colour-theme and darkness switches (and the stored choice) stay dark for
-- everyone whose profile lacks 'high-contrast' in permissions.features; this file
-- adds the flag to the chosen few: accounts limited to the Projects section.
-- NOT RUN YET — run in the Supabase SQL editor on the owner's "run it".
--
-- PREVIEW (read-only — run first; expect the Projects trial accounts):
--   select id, email, permissions->>'scope' as scope, permissions->'features' as features
--   from public.user_profiles
--   where permissions->>'scope' = 'Projects'
--     and not (coalesce(permissions->'features', '[]'::jsonb) ? 'high-contrast');

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

-- Backup: the affected rows' current permissions (restore with jsonb_set back).
DROP TABLE IF EXISTS public.user_profiles_backup_2026_10_25;
CREATE TABLE public.user_profiles_backup_2026_10_25 AS
SELECT id, email, permissions
FROM public.user_profiles
WHERE permissions->>'scope' = 'Projects'
  AND NOT (COALESCE(permissions->'features', '[]'::jsonb) ? 'high-contrast');

UPDATE public.user_profiles
SET permissions = jsonb_set(
  COALESCE(permissions, '{}'::jsonb),
  '{features}',
  COALESCE(permissions->'features', '[]'::jsonb) || '"high-contrast"'::jsonb
)
WHERE permissions->>'scope' = 'Projects'
  AND NOT (COALESCE(permissions->'features', '[]'::jsonb) ? 'high-contrast');

-- Verify: every Projects-scoped account now carries the flag (expect 0 rows).
SELECT id, email, permissions->'features' AS features
FROM public.user_profiles
WHERE permissions->>'scope' = 'Projects'
  AND NOT (COALESCE(permissions->'features', '[]'::jsonb) ? 'high-contrast');

COMMIT;
