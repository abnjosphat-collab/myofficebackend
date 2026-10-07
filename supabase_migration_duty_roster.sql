-- Duty Roster — who is the duty official for each date range.
-- department NULL = mine-wide official; otherwise the official for that department.
-- Run once in Supabase SQL editor.

CREATE TABLE IF NOT EXISTS duty_roster (
  id                              bigserial    PRIMARY KEY,
  employee_id                     text         NOT NULL,
  employee_name                   text         NOT NULL,
  phone                           text,
  department                      text,
  date_from                       date         NOT NULL,
  date_to                         date         NOT NULL CHECK (date_to >= date_from),
  note                            text,
  created_at                      timestamptz  NOT NULL DEFAULT now(),
  updated_at                      timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_duty_roster_dates ON duty_roster (date_from, date_to);
CREATE INDEX IF NOT EXISTS idx_duty_roster_employee_id ON duty_roster (employee_id);

NOTIFY pgrst, 'reload schema';
