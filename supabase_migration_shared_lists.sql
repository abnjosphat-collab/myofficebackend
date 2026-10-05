-- Shared lists: the PPE order list and saved quotations, kept on the server instead of one browser.
-- Run in the Supabase SQL editor. Adds two new tables and changes nothing that exists; safe to run twice.

-- 1. PPE order list: one row per PPE record flagged for ordering. `entry` is the line exactly as the page builds it
--    (employee, PPE type, item, size, expiry date), so the page can grow fields without a migration.
create table if not exists public.ppe_order_list (
  record_id  text primary key,
  entry      jsonb not null,
  added_at   timestamptz not null default now(),
  added_by   text not null default ''
);

-- 2. Saved quotations: one row per quotation number. `draft` is the quotation as the generator holds it.
create table if not exists public.saved_quotations (
  id          text primary key,
  draft       jsonb not null,
  saved_at    timestamptz not null default now(),
  saved_by    text not null default '',
  saved_by_id text not null default '',
  updated_at  timestamptz not null default now()
);
create index if not exists saved_quotations_saved_at_idx on public.saved_quotations(saved_at desc);

-- 3. RLS: the backend (service role) does the reading and writing; signed-in users may also read and write directly, like the other tables.
alter table public.ppe_order_list   enable row level security;
alter table public.saved_quotations enable row level security;

drop policy if exists "auth_all_ppe_order_list"          on public.ppe_order_list;
drop policy if exists "service_role_ppe_order_list"      on public.ppe_order_list;
drop policy if exists "auth_all_saved_quotations"        on public.saved_quotations;
drop policy if exists "service_role_saved_quotations"    on public.saved_quotations;

create policy "auth_all_ppe_order_list"       on public.ppe_order_list   for all to authenticated using (true) with check (true);
create policy "service_role_ppe_order_list"   on public.ppe_order_list   for all to service_role  using (true) with check (true);
create policy "auth_all_saved_quotations"     on public.saved_quotations for all to authenticated using (true) with check (true);
create policy "service_role_saved_quotations" on public.saved_quotations for all to service_role  using (true) with check (true);

notify pgrst, 'reload schema';
