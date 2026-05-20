-- =====================================================================
-- TIMESHEETS TABLE — initial creation
-- Run this in: Supabase Dashboard → SQL Editor
--
-- Creates the timesheets table used by the Maintenance Timesheets page.
-- Safe to run multiple times (uses IF NOT EXISTS / DO $$ blocks).
-- =====================================================================

-- ── 1. Create the table ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.timesheets (
    id                      BIGSERIAL PRIMARY KEY,
    employee_id             INTEGER NOT NULL,
    date                    DATE NOT NULL,
    start_time              TEXT,
    end_time                TEXT,
    regular_hours           NUMERIC(6,2) DEFAULT 0,
    overtime_hours          NUMERIC(6,2) DEFAULT 0,
    holiday_overtime_hours  NUMERIC(6,2) DEFAULT 0,
    nightshift_hours        NUMERIC(6,2) DEFAULT 0,
    standby_allowance       BOOLEAN DEFAULT FALSE,
    total_hours             NUMERIC(6,2) DEFAULT 0,
    status                  TEXT DEFAULT 'work',
    notes                   TEXT,
    overtime_periods        JSONB DEFAULT '[]'::jsonb,
    callout_overtime_hours  NUMERIC(6,2) DEFAULT 0,
    callout_count           INTEGER DEFAULT 0,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ── 2. Unique constraint: one entry per employee per day ─────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema    = 'public'
          AND table_name      = 'timesheets'
          AND constraint_name = 'timesheets_employee_date_unique'
    ) THEN
        ALTER TABLE public.timesheets
            ADD CONSTRAINT timesheets_employee_date_unique
            UNIQUE (employee_id, date);
        RAISE NOTICE 'UNIQUE constraint added on (employee_id, date)';
    ELSE
        RAISE NOTICE 'UNIQUE constraint already exists — skipped';
    END IF;
END $$;

-- ── 3. Index for fast range queries (period fetch) ───────────────────
CREATE INDEX IF NOT EXISTS timesheets_date_idx        ON public.timesheets (date);
CREATE INDEX IF NOT EXISTS timesheets_employee_idx    ON public.timesheets (employee_id);
CREATE INDEX IF NOT EXISTS timesheets_emp_date_idx    ON public.timesheets (employee_id, date);

-- ── 4. Auto-update updated_at on row change ──────────────────────────
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS timesheets_updated_at ON public.timesheets;
CREATE TRIGGER timesheets_updated_at
    BEFORE UPDATE ON public.timesheets
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ── 5. Row Level Security (RLS) — allow all authenticated users ──────
ALTER TABLE public.timesheets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "timesheets_all_access" ON public.timesheets;
CREATE POLICY "timesheets_all_access"
    ON public.timesheets
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- ── 6. Verify ────────────────────────────────────────────────────────
SELECT
    column_name,
    data_type,
    column_default,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'timesheets'
ORDER BY ordinal_position;
