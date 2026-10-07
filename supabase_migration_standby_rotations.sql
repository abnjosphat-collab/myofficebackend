-- Standby Rotations — ordered on-standby sequences: who holds each standby week,
-- in what order, with which crew. Members rotate every week_length_days from
-- cycle_start_date; the holder for a given week is computed, not stored.
-- Run once in Supabase SQL editor.

CREATE TABLE IF NOT EXISTS standby_rotations (
  id                              bigserial    PRIMARY KEY,
  name                            text         NOT NULL,
  section                         text,
  members                         jsonb        NOT NULL DEFAULT '[]'::jsonb,
  week_length_days                integer      NOT NULL DEFAULT 7 CHECK (week_length_days >= 1 AND week_length_days <= 31),
  cycle_start_date                date         NOT NULL,
  is_active                       boolean      NOT NULL DEFAULT true,
  notes                           text,
  created_at                      timestamptz  NOT NULL DEFAULT now(),
  updated_at                      timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_standby_rotations_active ON standby_rotations (is_active);

NOTIFY pgrst, 'reload schema';
