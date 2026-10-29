-- PPE premature failure: an issue can be marked as replacing an item that failed before its expiry, with an optional
-- note of what failed (owner request, 29 Oct 2026). Adds two columns to ppe_records; no existing row changes value
-- (premature_failure defaults to false, failure_reason to null). Must run BEFORE the backend that writes them is live:
-- the create endpoint sends premature_failure on every insert.
-- Rerunnable: IF NOT EXISTS on both columns; the guards check the end state.
--
-- Rollback: alter table public.ppe_records drop column premature_failure, drop column failure_reason;
begin;

do $$
begin
  if to_regclass('public.ppe_records') is null then
    raise exception 'ppe_records is missing. Nothing was changed.';
  end if;
end $$;

alter table public.ppe_records add column if not exists premature_failure boolean not null default false;
alter table public.ppe_records add column if not exists failure_reason text;

do $$
declare
  cols int;
  flagged int;
begin
  select count(*) into cols from information_schema.columns
  where table_schema = 'public' and table_name = 'ppe_records' and column_name in ('premature_failure', 'failure_reason');
  if cols <> 2 then
    raise exception 'Expected both new columns, found %. Rolled back.', cols;
  end if;
  -- A fresh column must not have flagged anything: only the app sets it.
  select count(*) into flagged from public.ppe_records where premature_failure or failure_reason is not null;
  raise notice 'premature_failure and failure_reason present; % rows flagged.', flagged;
end $$;

commit;
