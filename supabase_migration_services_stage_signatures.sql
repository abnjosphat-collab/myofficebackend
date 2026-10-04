-- Services Tracker: stage signatures.
-- Run in the Supabase SQL editor. Each approval stage is signed with a drawn, uploaded or saved signature; the image is kept here
-- as {"planning": "data:image/png;base64,...", "finance": "...", ...} (keys: planning, engineering_manager, finance, gm, stores, payment).
-- Safe to run twice. It adds one column and changes no existing data.

ALTER TABLE public.services
  ADD COLUMN IF NOT EXISTS stage_signatures jsonb NOT NULL DEFAULT '{}'::jsonb;

NOTIFY pgrst, 'reload schema';
