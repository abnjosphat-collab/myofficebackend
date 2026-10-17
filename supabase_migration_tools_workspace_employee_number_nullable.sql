-- Lets a Tools employee be recorded before their mine number is known: a real
-- NULL, not a placeholder number, so unknown numbers never collide with real
-- ones. The case-insensitive unique index still rejects duplicate real
-- numbers; NULLs never equal each other and are admitted freely.
-- Run once in the Supabase SQL editor AFTER deploying the backend that
-- accepts a missing employee_number. Safe to re-run (DROP NOT NULL on an
-- already-nullable column is a no-op).
-- Deploy-time proof (scratch table in a dev project, not production):
-- two rows with NULL employee_number must both insert; two rows with the
-- same real number (any case) must conflict.

ALTER TABLE public.tools_workspace_employees ALTER COLUMN employee_number DROP NOT NULL;
