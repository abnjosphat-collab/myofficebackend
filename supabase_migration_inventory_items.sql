-- Inventory register: a real table for app/routers/inventory.py. Until now the page kept items in each
-- browser's localStorage and the backend router held sample data in memory. The page moves a browser's
-- local items up once on its first visit after this change. Stock status is computed on read.

create table if not exists public.inventory_items (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  sku text not null default '',
  category text not null default '',
  description text not null default '',
  current_stock integer not null default 0,
  min_stock integer not null default 0,
  max_stock integer not null default 0,
  unit text not null default '',
  cost numeric(12, 2) not null default 0,
  supplier text not null default '',
  location text not null default '',
  last_restocked timestamptz,
  created_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_inventory_items_name on public.inventory_items (name);
create index if not exists idx_inventory_items_category on public.inventory_items (category);

alter table public.inventory_items enable row level security;
create policy service_role_inventory_items on public.inventory_items for all to service_role using (true) with check (true);
