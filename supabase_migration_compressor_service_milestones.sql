-- ============================================================
-- Compressor hour-service milestones — Run in Supabase SQL Editor
--
-- Owner, 13 Oct 2026: the service ladder is 400 / 800 / 1000 / 2000 / 4000 /
-- 8000 running hours (replaces 1000 / 2000 / 4000 / 8000 / 16000). The backend
-- constant SERVICE_MILESTONES and the frontend SERVICE_INTERVALS already match;
-- this brings the service_intervals rows the reading-post check reads along.
--
-- Every step is safe to run twice. Removing a superseded milestone row cannot
-- strand history: service_records stores its own service_type text and running
-- hours, never a foreign key to this table.
-- ============================================================

-- 1. Drop milestones that are no longer on the ladder.
delete from service_intervals
where interval_hours not in (400, 800, 1000, 2000, 4000, 8000);

-- 2. Add the milestones that are missing (400 and 800 are new).
insert into service_intervals (interval_hours)
select v.hours
from (values (400), (800), (1000), (2000), (4000), (8000)) as v(hours)
where not exists (select 1 from service_intervals s where s.interval_hours = v.hours);

-- Check: exactly these six rows, ascending.
select interval_hours from service_intervals order by interval_hours;
