begin;

alter table public.tools_workspace_employees
  add column if not exists supervisor_name text;

update storage.buckets
set allowed_mime_types=array['audio/webm','audio/ogg','audio/mp4','audio/mpeg','audio/wav','audio/x-wav']
where id='tools-workspace-feedback';

commit;
