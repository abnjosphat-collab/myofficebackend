-- ============================================================
-- Accounting & Financial Module Migration — Run in Supabase SQL Editor
-- Four tables backing the Finance & Accounting module: sales transactions
-- (human-readable sequential receipt number allocated server-side, see
-- app/routers/accounting.py), business expenses, and a simple
-- assets/liabilities register for a net-worth figure. Prefixed
-- `accounting_` to avoid confusion with the unrelated "Assets" (equipment)
-- module, which already exists under the `equipment` table.
-- ============================================================

create table if not exists public.accounting_transactions (
  id                bigint generated always as identity primary key,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  transaction_date  date not null default current_date,
  receipt_number    text,
  service_type      text not null,
  description       text,
  client_name       text,
  amount            numeric(14, 2) not null check (amount > 0),
  notes             text
);
create index if not exists accounting_transactions_date_idx on public.accounting_transactions(transaction_date);
create index if not exists accounting_transactions_service_type_idx on public.accounting_transactions(service_type);
create unique index if not exists uq_accounting_transactions_receipt_number
  on public.accounting_transactions(receipt_number) where receipt_number is not null;
drop trigger if exists accounting_transactions_updated_at on public.accounting_transactions;
create trigger accounting_transactions_updated_at before update on public.accounting_transactions
  for each row execute function public.set_updated_at();
alter table public.accounting_transactions enable row level security;
drop policy if exists "auth_all_accounting_transactions" on public.accounting_transactions;
drop policy if exists "service_role_accounting_transactions" on public.accounting_transactions;
create policy "auth_all_accounting_transactions" on public.accounting_transactions for all to authenticated using (true) with check (true);
create policy "service_role_accounting_transactions" on public.accounting_transactions for all to service_role using (true) with check (true);

create table if not exists public.accounting_expenses (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  expense_date   date not null default current_date,
  category       text not null,
  vendor         text,
  description    text,
  amount         numeric(14, 2) not null check (amount > 0),
  payment_method text,
  notes          text
);
create index if not exists accounting_expenses_date_idx on public.accounting_expenses(expense_date);
create index if not exists accounting_expenses_category_idx on public.accounting_expenses(category);
drop trigger if exists accounting_expenses_updated_at on public.accounting_expenses;
create trigger accounting_expenses_updated_at before update on public.accounting_expenses
  for each row execute function public.set_updated_at();
alter table public.accounting_expenses enable row level security;
drop policy if exists "auth_all_accounting_expenses" on public.accounting_expenses;
drop policy if exists "service_role_accounting_expenses" on public.accounting_expenses;
create policy "auth_all_accounting_expenses" on public.accounting_expenses for all to authenticated using (true) with check (true);
create policy "service_role_accounting_expenses" on public.accounting_expenses for all to service_role using (true) with check (true);

create table if not exists public.accounting_assets (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  name           text not null,
  category       text not null,
  acquired_date  date,
  value          numeric(14, 2) not null default 0 check (value >= 0),
  notes          text
);
create index if not exists accounting_assets_category_idx on public.accounting_assets(category);
drop trigger if exists accounting_assets_updated_at on public.accounting_assets;
create trigger accounting_assets_updated_at before update on public.accounting_assets
  for each row execute function public.set_updated_at();
alter table public.accounting_assets enable row level security;
drop policy if exists "auth_all_accounting_assets" on public.accounting_assets;
drop policy if exists "service_role_accounting_assets" on public.accounting_assets;
create policy "auth_all_accounting_assets" on public.accounting_assets for all to authenticated using (true) with check (true);
create policy "service_role_accounting_assets" on public.accounting_assets for all to service_role using (true) with check (true);

create table if not exists public.accounting_liabilities (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  name        text not null,
  category    text not null,
  due_date    date,
  amount      numeric(14, 2) not null default 0 check (amount >= 0),
  notes       text
);
create index if not exists accounting_liabilities_category_idx on public.accounting_liabilities(category);
create index if not exists accounting_liabilities_due_date_idx on public.accounting_liabilities(due_date);
drop trigger if exists accounting_liabilities_updated_at on public.accounting_liabilities;
create trigger accounting_liabilities_updated_at before update on public.accounting_liabilities
  for each row execute function public.set_updated_at();
alter table public.accounting_liabilities enable row level security;
drop policy if exists "auth_all_accounting_liabilities" on public.accounting_liabilities;
drop policy if exists "service_role_accounting_liabilities" on public.accounting_liabilities;
create policy "auth_all_accounting_liabilities" on public.accounting_liabilities for all to authenticated using (true) with check (true);
create policy "service_role_accounting_liabilities" on public.accounting_liabilities for all to service_role using (true) with check (true);
