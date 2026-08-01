-- Replaces the single-string responsible_person with responsible_people (a text
-- array), so a task/event can have more than one owner. Run once in the Supabase
-- SQL editor, after supabase_migration_tasks_events_comments.sql.

ALTER TABLE public.tasks_events ADD COLUMN IF NOT EXISTS responsible_people TEXT[] NOT NULL DEFAULT '{}';

UPDATE public.tasks_events
SET responsible_people = ARRAY[responsible_person]
WHERE responsible_person IS NOT NULL AND responsible_person <> '' AND responsible_people = '{}';

ALTER TABLE public.tasks_events DROP COLUMN IF EXISTS responsible_person;
