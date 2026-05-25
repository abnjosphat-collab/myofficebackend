-- ============================================================
-- Services Tracker Migration
-- Run in Supabase SQL Editor
-- ============================================================

-- 1. Services table (flat schema — one row per service record)
create table if not exists public.services (
  id                    uuid primary key default gen_random_uuid(),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  -- Basic information
  date                  date,
  description           text not null default '',
  supplier              text not null default '',
  contact_person        text not null default '',
  requisition_number    text not null default '',
  invoice_number        text not null default '',
  order_number          text not null default '',
  amount                text not null default '',
  category              text not null default '',
  general_comments      text not null default '',

  -- Stage 1: Planning
  planning_signed       boolean not null default false,
  planning_signed_by    text not null default '',
  planning_signed_date  date,
  planning_comments     text not null default '',

  -- Stage 2: Engineering Manager
  eng_mgr_signed        boolean not null default false,
  eng_mgr_signed_by     text not null default '',
  eng_mgr_signed_date   date,
  eng_mgr_comments      text not null default '',

  -- Stage 3: Finance
  finance_signed        boolean not null default false,
  finance_signed_by     text not null default '',
  finance_signed_date   date,
  finance_comments      text not null default '',

  -- Stage 4: General Manager
  gm_signed             boolean not null default false,
  gm_signed_by          text not null default '',
  gm_signed_date        date,
  gm_comments           text not null default '',

  -- Stage 5: Stores / GRV
  stores_signed         boolean not null default false,
  stores_signed_by      text not null default '',
  stores_signed_date    date,
  stores_comments       text not null default '',
  stores_grv_number     text not null default '',

  -- Stage 6: Payment
  payment_done          boolean not null default false,
  payment_paid_by       text not null default '',
  payment_date          date,
  payment_reference     text not null default '',
  payment_comments      text not null default ''
);

-- 2. Attachments table (scanned hard copies linked to a service record)
create table if not exists public.service_attachments (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  service_id    uuid not null references public.services(id) on delete cascade,
  filename      text not null,
  file_url      text not null default '',
  storage_path  text not null default '',
  file_size     bigint not null default 0,
  mime_type     text not null default 'application/octet-stream'
);

-- 3. Useful indexes
create index if not exists services_date_idx          on public.services(date desc);
create index if not exists services_created_at_idx    on public.services(created_at desc);
create index if not exists services_supplier_idx      on public.services(supplier);
create index if not exists attachments_service_id_idx on public.service_attachments(service_id);

-- 4. Auto-update updated_at trigger
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end;
$$;

drop trigger if exists services_updated_at on public.services;
create trigger services_updated_at
  before update on public.services
  for each row execute function public.set_updated_at();

-- ============================================================
-- 5. Supabase Storage — run these steps manually in the
--    Supabase Dashboard → Storage → New bucket:
--
--    Bucket name : service-attachments
--    Public      : true   (enables direct download links)
--
--    Or via SQL:
-- insert into storage.buckets (id, name, public)
-- values ('service-attachments', 'service-attachments', true)
-- on conflict do nothing;
-- ============================================================

-- 6. RLS (optional — disable if you use service-role key only)
alter table public.services            enable row level security;
alter table public.service_attachments enable row level security;

-- Allow all authenticated users to read/write (adjust as needed)
drop policy if exists "auth_all_services"     on public.services;
drop policy if exists "auth_all_attachments"  on public.service_attachments;
drop policy if exists "service_role_services" on public.services;
drop policy if exists "service_role_attachments" on public.service_attachments;

create policy "auth_all_services"
  on public.services for all to authenticated using (true) with check (true);

create policy "auth_all_attachments"
  on public.service_attachments for all to authenticated using (true) with check (true);

-- Allow service role (backend) unrestricted access
create policy "service_role_services"
  on public.services for all to service_role using (true) with check (true);

create policy "service_role_attachments"
  on public.service_attachments for all to service_role using (true) with check (true);
