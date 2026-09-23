-- Per-account acknowledgement for active Tools & Equipment alerts.
begin;
create table if not exists public.tools_workspace_notification_reads (
  account_id uuid not null references public.tools_workspace_accounts(id) on delete cascade,
  alert_key text not null,
  read_at timestamptz not null default now(),
  primary key (account_id, alert_key)
);
create index if not exists tools_workspace_notification_reads_account_idx
  on public.tools_workspace_notification_reads(account_id, read_at desc);
alter table public.tools_workspace_notification_reads enable row level security;
commit;
