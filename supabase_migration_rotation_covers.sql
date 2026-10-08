-- Rotation Covers — stand-ins: who holds in place of whom, for which dates.
-- kind + rotation_id point at a standby_rotations or duty_rotations row; the
-- absent member may be a rotation lead, a crew member, or a duty official.
-- One person holds at most one cover per rotation over any date (the API
-- refuses a second with 409). Run once in Supabase SQL editor
-- (or scripts/apply_migration.py).

CREATE TABLE IF NOT EXISTS rotation_covers (
  id                              bigserial    PRIMARY KEY,
  kind                            text         NOT NULL CHECK (kind IN ('standby', 'duty')),
  rotation_id                     bigint       NOT NULL,
  absent_employee_id              text         NOT NULL,
  absent_employee_name            text         NOT NULL,
  cover_employee_id               text         NOT NULL,
  cover_employee_name             text         NOT NULL,
  cover_phone                     text,
  date_from                       date         NOT NULL,
  date_to                         date         NOT NULL CHECK (date_to >= date_from),
  reason                          text,
  created_at                      timestamptz  NOT NULL DEFAULT now(),
  updated_at                      timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rotation_covers_rotation ON rotation_covers (kind, rotation_id);
CREATE INDEX IF NOT EXISTS idx_rotation_covers_absent ON rotation_covers (absent_employee_id);
CREATE INDEX IF NOT EXISTS idx_rotation_covers_dates ON rotation_covers (date_from, date_to);

NOTIFY pgrst, 'reload schema';
