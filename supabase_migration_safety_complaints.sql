-- Migration: create safety_complaints table
-- Run this in the Supabase SQL editor

create table if not exists safety_complaints (
  id                  text primary key,
  date                date not null,
  raised_by           text default '',
  issue_raised        text not null,
  category            text default 'General',
  priority            text default 'medium' check (priority in ('low','medium','high','critical')),
  section             text default 'General',
  location            text default '',
  action_plan         text default '',
  by_who              text default '',
  by_when             date,
  supervisor_name     text default '',
  supervisor_signature text default '',
  date_closed         date,
  status              text default 'open' check (status in ('open','in-progress','closed','overdue')),
  submitted_at        timestamptz default now()
);

-- Enable RLS (Row Level Security)
alter table safety_complaints enable row level security;

-- Allow all operations for authenticated users (adjust to your policy)
create policy "Allow all for authenticated" on safety_complaints
  for all using (true) with check (true);

-- Index for common filter queries
create index if not exists idx_safety_complaints_date   on safety_complaints (date desc);
create index if not exists idx_safety_complaints_status on safety_complaints (status);
