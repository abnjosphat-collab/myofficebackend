-- Duty Rotations — ordered repeating sequences of duty officials: who answers for
-- the mine (or one department) each stint, in what order. Members rotate every
-- week_length_days from cycle_start_date; the holder for a given date is
-- computed, not stored. One-off duty_roster entries override the rotation where
-- their dates overlap. Run once in Supabase SQL editor (or scripts/apply_migration.py).

CREATE TABLE IF NOT EXISTS duty_rotations (
  id                              bigserial    PRIMARY KEY,
  name                            text         NOT NULL,
  department                      text,
  members                         jsonb        NOT NULL DEFAULT '[]'::jsonb,
  week_length_days                integer      NOT NULL DEFAULT 7 CHECK (week_length_days >= 1 AND week_length_days <= 31),
  cycle_start_date                date         NOT NULL,
  is_active                       boolean      NOT NULL DEFAULT true,
  notes                           text,
  created_at                      timestamptz  NOT NULL DEFAULT now(),
  updated_at                      timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_duty_rotations_active ON duty_rotations (is_active);
CREATE INDEX IF NOT EXISTS idx_duty_rotations_department ON duty_rotations (department);

NOTIFY pgrst, 'reload schema';
