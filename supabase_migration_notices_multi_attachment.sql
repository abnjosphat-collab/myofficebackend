-- Replaces the single attachment_name/attachment_url/attachment_size columns with
-- `attachments` (a JSONB array of {name, url, size} objects), so a notice can carry
-- more than one file. Run once in the Supabase SQL editor.

ALTER TABLE public.notices ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]'::jsonb;

UPDATE public.notices
SET attachments = jsonb_build_array(jsonb_build_object(
  'name', attachment_name,
  'url', attachment_url,
  'size', attachment_size
))
WHERE attachment_url IS NOT NULL AND attachment_url <> '' AND attachments = '[]'::jsonb;

ALTER TABLE public.notices DROP COLUMN IF EXISTS attachment_name;
ALTER TABLE public.notices DROP COLUMN IF EXISTS attachment_url;
ALTER TABLE public.notices DROP COLUMN IF EXISTS attachment_size;
