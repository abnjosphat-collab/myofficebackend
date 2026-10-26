-- Mono dark trial: flag BOTH Projects-scoped accounts for the appearance trial.
-- The light/dark switch (and the stored choice) stays dark for everyone whose
-- profile lacks 'appearance_themes' in permissions.features; this file adds the
-- flag to every account scoped to Projects (2 live on 26 Oct 2026). Idempotent:
-- accounts already flagged are skipped.
-- Run with scripts/apply_migration.py on the owner's "run it".
--
-- PREVIEW (read-only — run first; expect the 2 Projects accounts):
--   select id, email, permissions->'features' as features
--   from public.user_profiles
--   where permissions->>'scope' = 'Projects'
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

-- Guardrail: at least one Projects-scoped account must exist (never run blind).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.user_profiles WHERE permissions->>'scope' = 'Projects') THEN
    RAISE EXCEPTION 'no Projects-scoped accounts found';
  END IF;
END
$$;

-- Backup: current permissions of every Projects account (restore with jsonb_set back).
DROP TABLE IF EXISTS public.user_profiles_backup_2026_10_26;
CREATE TABLE public.user_profiles_backup_2026_10_26 AS
SELECT id, email, permissions
FROM public.user_profiles
WHERE permissions->>'scope' = 'Projects';

UPDATE public.user_profiles
SET permissions = jsonb_set(
  COALESCE(permissions, '{}'::jsonb),
  '{features}',
  COALESCE(permissions->'features', '[]'::jsonb) || '"appearance_themes"'::jsonb
)
WHERE permissions->>'scope' = 'Projects'
  AND NOT (COALESCE(permissions->'features', '[]'::jsonb) ? 'appearance_themes');

-- Verify: every Projects account now carries the flag (expect 0 rows).
SELECT id, email, permissions->'features' AS features
FROM public.user_profiles
WHERE permissions->>'scope' = 'Projects'
  AND NOT (COALESCE(permissions->'features', '[]'::jsonb) ? 'appearance_themes');

COMMIT;
