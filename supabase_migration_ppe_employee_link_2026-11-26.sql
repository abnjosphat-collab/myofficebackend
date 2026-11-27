BEGIN;

ALTER TABLE public.ppe_records
    ADD COLUMN IF NOT EXISTS employee_db_id bigint REFERENCES public.employees(id) ON DELETE RESTRICT,
    ALTER COLUMN employee_id DROP NOT NULL;

ALTER TABLE public.ppe_events
    ADD COLUMN IF NOT EXISTS employee_db_id bigint REFERENCES public.employees(id) ON DELETE RESTRICT,
    ALTER COLUMN employee_id DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ppe_one_record_per_linked_issue
    ON public.ppe_records (employee_db_id, lower(btrim(ppe_type)), issue_date)
    WHERE employee_db_id IS NOT NULL
      AND coalesce(lower(status), 'active') NOT IN ('returned', 'replaced', 'lost', 'disposed', 'cancelled');

CREATE INDEX IF NOT EXISTS ppe_records_employee_db_id_idx ON public.ppe_records (employee_db_id);
CREATE INDEX IF NOT EXISTS ppe_events_employee_db_id_idx ON public.ppe_events (employee_db_id);

NOTIFY pgrst, 'reload schema';
COMMIT;
