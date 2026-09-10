-- ============================================================
-- SOP Library Migration — sop_documents + sop_revisions
-- Run in Supabase SQL Editor
-- ============================================================
--
-- sop_documents is the single source of truth for an SOP's current state.
-- Every successful save also writes a full snapshot into sop_revisions
-- (author + timestamp + change note) — that table is the audit/history
-- trail the "Revision History" UI section reads from; it is never edited,
-- only appended to. Deleting an SOP is always a soft-delete (deleted_at) —
-- there is deliberately no hard-delete path from the API.

-- Status/classification/risk-tier vocabulary matches Ozech's own SOP Governance
-- Manual (document lifecycle table + risk-tier table), not an invented enum:
--   status:         draft | pilot | effective | superseded | retired
--   classification: Internal | Confidential | Restricted
--   risk_tier:      1 (routine) | 2 (material) | 3 (critical) — nullable, a Draft
--                   SOP need not have a tier assigned yet.
create table if not exists public.sop_documents (
  id                uuid            primary key default gen_random_uuid(),

  code              text            not null,
  title             text            not null,
  department        text            not null default '',
  summary           text            not null default '',
  status            text            not null default 'draft',
  classification    text            not null default 'Internal',
  risk_tier         smallint,
  owner             text            not null default '',
  approver          text,
  supersedes        text,
  version           text            not null default '0.1',
  effective_date    date,
  next_review_date  date,
  tags              jsonb           not null default '[]'::jsonb,

  -- Structured sections, not one HTML blob — one text field per named part of the
  -- document, mirroring the governance manual's "mandatory SOP content" list
  -- (purpose, scope_exclusions, definitions, trigger_outcome,
  -- roles_responsibilities, inputs_dependencies, procedure, controls,
  -- exceptions_escalation, records_retention, measures_review, training).
  -- Keys are validated in the API layer, all optional — a Draft need not have
  -- every section filled in yet.
  sections          jsonb           not null default '{}'::jsonb,

  created_at        timestamptz     not null default now(),
  updated_at        timestamptz     not null default now(),
  created_by        text            not null default '',
  updated_by        text            not null default '',
  deleted_at        timestamptz,

  constraint sop_documents_status_check
    check (status in ('draft', 'pilot', 'effective', 'superseded', 'retired')),
  constraint sop_documents_classification_check
    check (classification in ('Internal', 'Confidential', 'Restricted')),
  constraint sop_documents_risk_tier_check
    check (risk_tier is null or risk_tier in (1, 2, 3))
);

-- Safety net if an earlier version of this migration (pre-governance-alignment,
-- status draft|in_review|approved|retired) was already applied: add the new
-- columns and widen the status constraint rather than requiring a hand-run ALTER.
alter table public.sop_documents add column if not exists classification text not null default 'Internal';
alter table public.sop_documents add column if not exists risk_tier      smallint;
alter table public.sop_documents add column if not exists supersedes     text;
alter table public.sop_documents drop constraint if exists sop_documents_status_check;
alter table public.sop_documents add constraint sop_documents_status_check
  check (status in ('draft', 'pilot', 'effective', 'superseded', 'retired'));

create unique index if not exists sop_documents_code_idx       on public.sop_documents (code);
create index if not exists        sop_documents_status_idx     on public.sop_documents (status);
create index if not exists        sop_documents_department_idx on public.sop_documents (department);
create index if not exists        sop_documents_deleted_at_idx on public.sop_documents (deleted_at);
create index if not exists        sop_documents_review_idx     on public.sop_documents (next_review_date);

drop trigger if exists sop_documents_updated_at on public.sop_documents;
create trigger sop_documents_updated_at
  before update on public.sop_documents
  for each row execute function public.set_updated_at();

alter table public.sop_documents enable row level security;

drop policy if exists "auth_all_sop_documents"    on public.sop_documents;
drop policy if exists "service_role_sop_documents" on public.sop_documents;

create policy "auth_all_sop_documents"
  on public.sop_documents for all to authenticated using (true) with check (true);

create policy "service_role_sop_documents"
  on public.sop_documents for all to service_role using (true) with check (true);

-- ============================================================

create table if not exists public.sop_revisions (
  id              uuid        primary key default gen_random_uuid(),
  sop_id          uuid        not null references public.sop_documents(id) on delete cascade,
  revision_number integer     not null,
  snapshot        jsonb       not null,
  change_note     text        not null default '',
  author_email    text        not null default '',
  author_id       text,
  created_at      timestamptz not null default now(),

  constraint sop_revisions_unique_number unique (sop_id, revision_number)
);

create index if not exists sop_revisions_sop_id_idx on public.sop_revisions (sop_id, revision_number desc);

alter table public.sop_revisions enable row level security;

drop policy if exists "auth_all_sop_revisions"     on public.sop_revisions;
drop policy if exists "service_role_sop_revisions" on public.sop_revisions;

create policy "auth_all_sop_revisions"
  on public.sop_revisions for all to authenticated using (true) with check (true);

create policy "service_role_sop_revisions"
  on public.sop_revisions for all to service_role using (true) with check (true);

-- ============================================================
