-- Separate administration from departmental custody and add flexible specifications.
begin;

alter table public.tools_workspace_accounts
  add column if not exists role text,
  add column if not exists department text;

update public.tools_workspace_accounts
set role = case when coalesce(can_issue, false) then 'admin' else 'viewer' end
where role is null;

alter table public.tools_workspace_accounts
  alter column role set default 'viewer',
  alter column role set not null;

alter table public.tools_workspace_accounts
  drop constraint if exists tools_workspace_accounts_role_check;
alter table public.tools_workspace_accounts
  add constraint tools_workspace_accounts_role_check
  check (role in ('admin', 'issuer', 'viewer'));

update public.tools_workspace_accounts
set can_issue = (role = 'issuer');

alter table public.tools_workspace_equipment
  add column if not exists specifications jsonb not null default '{}'::jsonb;

commit;
