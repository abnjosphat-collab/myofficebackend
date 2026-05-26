-- ============================================================
-- Availabilities Table Migration
-- Run in Supabase SQL Editor
-- ============================================================

-- Create table if it doesn't already exist
-- availability_percentage is GENERATED so it cannot be set on insert/update
create table if not exists public.availabilities (
  id                      serial          primary key,
  equipment_id            integer         not null,
  date                    date            not null,
  operational_hours       numeric(10, 2)  not null default 0,
  breakdown_hours         numeric(10, 2)  not null default 0,
  availability_percentage numeric(5, 2)   generated always as (
    case when operational_hours > 0
         then round(((operational_hours - breakdown_hours) / operational_hours) * 100, 2)
         else 100
    end
  ) stored,
  status                  text            not null default 'operational',
  mtbf                    numeric(10, 2),
  mttr                    numeric(10, 2),
  notes                   text            not null default '',
  created_at              timestamptz     not null default now(),
  updated_at              timestamptz     not null default now()
);

-- Add new columns if the table already existed without them
alter table public.availabilities add column if not exists notes      text         not null default '';
alter table public.availabilities add column if not exists updated_at timestamptz;

-- Back-fill updated_at for rows that have null
update public.availabilities set updated_at = created_at where updated_at is null;

-- Indexes
create index if not exists avail_equipment_id_idx on public.availabilities (equipment_id);
create index if not exists avail_date_idx         on public.availabilities (date desc);

-- Auto-update updated_at trigger (reuses set_updated_at() from other migrations)
drop trigger if exists availabilities_updated_at on public.availabilities;
create trigger availabilities_updated_at
  before update on public.availabilities
  for each row execute function public.set_updated_at();

-- RLS
alter table public.availabilities enable row level security;

drop policy if exists "auth_all_availabilities"     on public.availabilities;
drop policy if exists "service_role_availabilities" on public.availabilities;

create policy "auth_all_availabilities"
  on public.availabilities for all to authenticated using (true) with check (true);

create policy "service_role_availabilities"
  on public.availabilities for all to service_role using (true) with check (true);

-- ============================================================
