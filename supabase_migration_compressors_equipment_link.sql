-- ============================================================
-- Compressors come from the Equipment register — Run in Supabase SQL Editor
--
-- The compressor list is the Equipment register's compressors (equipment.category starts with "Compressor"). The
-- `compressors` table keeps only what the register cannot hold: the operating status and the hour meters. Each tracked
-- row points at its register item through `equipment_id`; the register owns the name, model and location.
--
-- The backend (app/compressor_register.py) also creates a row for any register compressor that has none, the first
-- time the list is read, so step 4 only makes that happen straight away. Every step is safe to run twice.
--
-- BEFORE YOU RUN IT: the 13 rows now in `compressors` are placeholders (made-up names, one "Compressor 11" twice, a
-- "CompA"), and nothing depends on them: no daily readings, no service records and no schedules exist. Their old
-- hour totals were saved to Documents/Codex/2026-10-10/outputs/compressors-hours-before-reset-2026-10-10.csv.
-- ============================================================

-- 1. The hours reset to zero (the owner's instruction, 10 Oct 2026): every compressor starts again from nothing.
update compressors
set total_running_hours = 0, total_loaded_hours = 0,
    initial_total_running = 0, initial_total_loaded = 0;

-- 2. The link to the Equipment register: one tracked compressor per register item.
alter table compressors
  add column if not exists equipment_id integer references equipment(id) on delete set null;

create unique index if not exists compressors_equipment_id_key
  on compressors (equipment_id) where equipment_id is not null;

-- 3. Remove the placeholder rows: ones with no link to the register and nothing recorded against them. A row that has
--    readings or service records is kept, so nothing real can be lost here.
delete from compressors c
where c.equipment_id is null
  and not exists (select 1 from compressor_readings r where r.compressor_id = c.id)
  and not exists (select 1 from daily_entries d where d.compressor_id = c.id)
  and not exists (select 1 from service_records s where s.compressor_id = c.id);

-- 4. Start tracking every register compressor that is not tracked yet (CMP 01 to CMP 12 today).
--    The state starts as "standby"; the operator sets the real one from the compressors page.
insert into compressors (name, model, capacity, location, status, equipment_id, serial_number)
select coalesce(nullif(btrim(e.name), ''), nullif(btrim(e.equipment_id), ''), 'Equipment ' || e.id),
       coalesce(btrim(e.model), ''),
       '',
       coalesce(btrim(e.location), ''),
       'standby',
       e.id,
       nullif(btrim(e.serial_number), '')
from equipment e
where lower(coalesce(e.category, '')) like 'compressor%'
  and not exists (select 1 from compressors c where c.equipment_id = e.id);

-- Check: this should list the register's compressors, each with an equipment_id and zero hours.
select c.name, c.location, c.status, c.equipment_id, c.total_running_hours, c.total_loaded_hours
from compressors c order by c.name;
