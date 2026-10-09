-- Training & Certification register: a real table for app/routers/training.py.
-- Until now the router kept records in backend memory (seeded with mock people), so every
-- redeploy lost them. Nothing real is migrated: the in-memory list never persisted anything.
-- Certificate files go to the existing ams-documents bucket under training/ (no new bucket).
-- Status (Valid / Due Soon / Expired) is computed on read from expiry_date, so it is not stored.

create table if not exists public.training_certifications (
  id uuid primary key default gen_random_uuid(),
  employee_id text not null,
  employee_name text not null,
  department text not null default '',
  certification_name text not null,
  expiry_date date not null,
  required_refresher text not null default '',
  certificate_url text,
  certificate_path text,
  created_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_training_certifications_expiry on public.training_certifications (expiry_date);
create index if not exists idx_training_certifications_employee on public.training_certifications (employee_id);

-- The backend reads and writes with the service role, like every other register; no client-side access.
alter table public.training_certifications enable row level security;
create policy service_role_training_certifications on public.training_certifications for all to service_role using (true) with check (true);
