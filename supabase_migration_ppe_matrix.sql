-- PPE replacement matrix — per-item-type interval (months until expiry, from issue date).
-- Run this once in the Supabase SQL editor. Until it exists, the /api/ppe/matrix endpoints
-- fall back to the code defaults (PPE_MATRIX_DEFAULTS in app/routers/ppe.py): auto-calc and
-- Recalculate still work off the defaults; saving custom intervals needs this table.

CREATE TABLE IF NOT EXISTS ppe_matrix (
    ppe_type        TEXT PRIMARY KEY,
    interval_months INTEGER NOT NULL CHECK (interval_months BETWEEN 0 AND 120),  -- 0 = no expiry
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the company defaults (safe to re-run; existing rows are left untouched).
INSERT INTO ppe_matrix (ppe_type, interval_months) VALUES
    ('worksuit', 6), ('gumboots', 6), ('safety_shoes', 6),
    ('helmet', 24), ('Cap_lamp_belt', 24), ('pneumo_jacket', 24), ('harness', 24),
    ('vest', 3), ('glasses', 3), ('respirator', 1), ('rainsuit', 6),
    ('gloves', 0), ('overall', 6)   -- gloves: 0 = no expiry / not required
ON CONFLICT (ppe_type) DO NOTHING;
