-- Remove the two unneeded unmatched PPE cards (owner instruction 22 Oct 2026:
-- "Chifamba and Mafukidze we no longer need them"): Michael Chifamba (C0323,
-- 4 rows) and Tendayi Mafukidze (PM184, 3 rows), all status 'not_required'.
-- Same effect as the manager-only DELETE /api/ppe/{id} endpoint (which has no
-- guards); run here over the API path is unavailable without a user login.
-- Full rows are kept in ppe_deleted_unmatched_backup_2026_10_22.
-- RAN 2026-10-22 via scripts/apply_migration.py, then recorded in the ledger.
-- Safe to re-run: the guard aborts unless the exact 7 rows are still present.
begin;
do $$
declare n int;
begin
  select count(*) into n from public.ppe_records
  where id = any (array[176,177,178,270,256,257,258])
    and ((employee_id = 'C0323') or (employee_id = 'PM184'))
    and status = 'not_required';
  if n <> 7 then
    raise exception 'Expected the exact 7 Chifamba/Mafukidze rows still present, found %. Nothing was changed.', n;
  end if;
end $$;
create table public.ppe_deleted_unmatched_backup_2026_10_22 as
select * from public.ppe_records where id = any (array[176,177,178,270,256,257,258]);
delete from public.ppe_records where id = any (array[176,177,178,270,256,257,258]);
commit;
-- Verify: select count(*) from public.ppe_records
-- where employee_id in ('C0323','PM184');  -- expect 0
