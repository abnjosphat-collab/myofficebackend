-- Artisan Timesheets — formal monthly daily timesheet documents for Class 1 artisans.
-- Run once in Supabase SQL editor.

CREATE TABLE IF NOT EXISTS artisan_timesheets (
  id                              bigserial    PRIMARY KEY,
  employee_id                     text         NOT NULL,
  employee_db_id                  integer,
  employee_name                   text         NOT NULL,
  id_number                       text,
  year                            integer      NOT NULL CHECK (year >= 2000 AND year <= 2100),
  month                           integer      NOT NULL CHECK (month >= 1 AND month <= 12),
  shift_rate                      numeric,
  hourly_rate                     numeric,
  adjustment                      numeric      NOT NULL DEFAULT 0,
  daily_rows                      jsonb        NOT NULL DEFAULT '[]'::jsonb,
  compiled_by                     text,
  approved_electrical_foreman     text,
  approved_mechanical_foreman     text,
  authorized_by                   text,
  created_at                      timestamptz  NOT NULL DEFAULT now(),
  updated_at                      timestamptz  NOT NULL DEFAULT now(),
  UNIQUE (employee_id, year, month)
);

CREATE INDEX IF NOT EXISTS idx_artisan_timesheets_year_month ON artisan_timesheets (year, month);
CREATE INDEX IF NOT EXISTS idx_artisan_timesheets_employee_id ON artisan_timesheets (employee_id);

NOTIFY pgrst, 'reload schema';
