-- Tools Part 2: trade sections per tool. NOT RUN - written for the owner to review and run.
--
-- Adds trade_sections (text[]) to the Tools equipment register, then applies the owner-confirmed list
-- below: 72 active tools, 44 shared by both trades, 16 Mechanical-only, 12 Electrical-only.
-- A tool's sections decide who gains eligibility by section; individual approvals are untouched.
--
-- Guards: the file changes nothing unless every listed register number matches exactly one tool and all
-- 72 are still without sections. Every touched row is first copied to
-- tools_workspace_equipment_backup_2026_10_24. Writes made here bypass the app, so they are not in the
-- audit log. Rerunning after a successful apply raises instead of changing anything.

alter table tools_workspace_equipment add column if not exists trade_sections text[];

begin;
create table if not exists tools_workspace_equipment_backup_2026_10_24 (like tools_workspace_equipment including all);
alter table tools_workspace_equipment_backup_2026_10_24 enable row level security;
create temp table _confirmed (number text, sections text[]) on commit drop;
insert into _confirmed values
  ('PH-HEMS', ARRAY['Electrical','Mechanical']),
  ('PJT-HEMS-BJR-1', ARRAY['Electrical','Mechanical']),
  ('PJT-HEMS-GR1', ARRAY['Electrical','Mechanical']),
  ('PP-UG-ENG-OE-01', ARRAY['Electrical','Mechanical']),
  ('PP-UG-ENG-OE-03', ARRAY['Electrical','Mechanical']),
  ('PP-UG-ENG-OE-04', ARRAY['Electrical']),
  ('PP-UG-ENG-OE-05', ARRAY['Electrical']),
  ('PP-UG-ENG-OE-06', ARRAY['Electrical']),
  ('PP-UG-ENG-OE-07', ARRAY['Electrical','Mechanical']),
  ('PP-UG-ENG-OE-08', ARRAY['Electrical','Mechanical']),
  ('PP-UG-ENG-OE-09', ARRAY['Electrical']),
  ('PP-UG-ENG-OE-10', ARRAY['Electrical','Mechanical']),
  ('PP-UG-ENG-OE-11', ARRAY['Electrical']),
  ('PP-UG-ENG-OE-12', ARRAY['Electrical']),
  ('PP-UG-ENG-OE-13', ARRAY['Electrical']),
  ('PP-UG-ENG-OE-14', ARRAY['Mechanical']),
  ('PP-UG-ENG-OE-15', ARRAY['Mechanical']),
  ('PP-UG-ENG-OE-16', ARRAY['Mechanical']),
  ('PP-UG-ENG-OE-17', ARRAY['Mechanical']),
  ('PP-UG-ENG-OE-18', ARRAY['Mechanical']),
  ('PP-UG-ENG-OE-19', ARRAY['Electrical','Mechanical']),
  ('PP-UG-ENG-OE-20', ARRAY['Electrical','Mechanical']),
  ('PP-UG-ENG-OE-21', ARRAY['Electrical','Mechanical']),
  ('PP-UG-ENG-OE-22', ARRAY['Electrical','Mechanical']),
  ('PP-UG-ENG-OE-23', ARRAY['Mechanical']),
  ('PP-UG-ENG-OE-24', ARRAY['Electrical','Mechanical']),
  ('PPUG-01-1W', ARRAY['Mechanical']),
  ('PPUG-01-BW', ARRAY['Mechanical']),
  ('PPUG-01-CDR', ARRAY['Electrical','Mechanical']),
  ('PPUG-01-ENG', ARRAY['Electrical','Mechanical']),
  ('PPUG-01-EX', ARRAY['Electrical']),
  ('PPUG-01-GR', ARRAY['Electrical','Mechanical']),
  ('PPUG-01-HAB', ARRAY['Electrical','Mechanical']),
  ('PPUG-01-MD', ARRAY['Electrical','Mechanical']),
  ('PPUG-01-PD', ARRAY['Electrical','Mechanical']),
  ('PPUG-01-PG', ARRAY['Electrical','Mechanical']),
  ('PPUG-01-W', ARRAY['Mechanical']),
  ('PPUG-02-BG', ARRAY['Electrical','Mechanical']),
  ('PPUG-02-BW', ARRAY['Mechanical']),
  ('PPUG-02-EX', ARRAY['Electrical']),
  ('PPUG-02-MD', ARRAY['Electrical','Mechanical']),
  ('PPUG-02-MEG', ARRAY['Electrical']),
  ('PPUG-02-W', ARRAY['Mechanical']),
  ('PPUG-03-BG', ARRAY['Electrical','Mechanical']),
  ('PPUG-03-BW', ARRAY['Mechanical']),
  ('PPUG-03-EX', ARRAY['Electrical']),
  ('PPUG-03-GR', ARRAY['Electrical','Mechanical']),
  ('PPUG-03-W', ARRAY['Mechanical']),
  ('PPUG-04-BG', ARRAY['Electrical','Mechanical']),
  ('PPUG-04-BW', ARRAY['Mechanical']),
  ('PPUG-04-ENG', ARRAY['Electrical','Mechanical']),
  ('PPUG-04-W', ARRAY['Mechanical']),
  ('PPUG-05-ENG', ARRAY['Electrical','Mechanical']),
  ('PPUG-05-W', ARRAY['Mechanical']),
  ('PPUG-07-GR', ARRAY['Electrical','Mechanical']),
  ('PPUG-08-GR', ARRAY['Electrical','Mechanical']),
  ('PPUG-09-GR', ARRAY['Electrical','Mechanical']),
  ('PPUG-HB-02', ARRAY['Electrical']),
  ('PRO-02-GR', ARRAY['Electrical','Mechanical']),
  ('PRO-03-GR', ARRAY['Electrical','Mechanical']),
  ('PRO-07-GR', ARRAY['Electrical','Mechanical']),
  ('PRO-08-GR', ARRAY['Electrical','Mechanical']),
  ('PRO-09-GR', ARRAY['Electrical','Mechanical']),
  ('PRO2', ARRAY['Electrical','Mechanical']),
  ('PRO3', ARRAY['Electrical','Mechanical']),
  ('PRO4', ARRAY['Electrical','Mechanical']),
  ('PRO5', ARRAY['Electrical','Mechanical']),
  ('PRO6', ARRAY['Electrical','Mechanical']),
  ('UG-BL-01', ARRAY['Electrical','Mechanical']),
  ('UG-BL-02', ARRAY['Electrical','Mechanical']),
  ('UG-PP-01', ARRAY['Electrical','Mechanical']),
  ('UG-PROJ-PF-01', ARRAY['Electrical','Mechanical']);

do $$
declare confirmed int; matched int; ready int;
begin
  select count(*) into confirmed from _confirmed;
  select count(*) into matched from _confirmed c join tools_workspace_equipment t on t.register_number = c.number;
  select count(*) into ready from _confirmed c join tools_workspace_equipment t on t.register_number = c.number where t.trade_sections is null;
  if confirmed <> 72 or matched <> 72 then
    raise exception 'Expected 72 listed numbers matching 72 tools, found % listed and % matched. Nothing was changed.', confirmed, matched;
  end if;
  if ready <> 72 then
    raise exception 'Expected all 72 tools still without sections, found %. Nothing was changed.', ready;
  end if;
end $$;

insert into tools_workspace_equipment_backup_2026_10_24
select t.* from tools_workspace_equipment t
join _confirmed c on c.number = t.register_number
where t.trade_sections is null
  and not exists (select 1 from tools_workspace_equipment_backup_2026_10_24 b where b.id = t.id);

update tools_workspace_equipment t set trade_sections = c.sections
from _confirmed c
where c.number = t.register_number
  and t.trade_sections is null
  and exists (select 1 from tools_workspace_equipment_backup_2026_10_24 b where b.id = t.id);
commit;
