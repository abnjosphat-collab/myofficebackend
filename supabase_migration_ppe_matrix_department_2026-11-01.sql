-- Mining-scoped PPE replacement matrix + Mining policy seed. Run once in the
-- Supabase SQL editor AFTER deploying the backend that reads it (batch 2).
-- Until this runs, the scoped-matrix API keeps serving the company matrix
-- (reads degrade gracefully); scoped PUT writes will fail loudly instead of
-- silently writing company rows.
--
-- Source of the Mining intervals: Mining PPE workbook
-- "PPE CARDS _054942.xlsx" (SHA256 1ce9a6e426a5ac0a4412c783f366d7d20cfe217b49a5738b154e572559c),
-- Settings sheet policy column, with Harness = 24 and Jackets = 24 confirmed by
-- the owner on 2026-11-01 (both were blank/unknown in the source workbook).
-- Provenance of each seeded value is therefore reviewable here; no guessed
-- corrections are baked in.

ALTER TABLE ppe_matrix ADD COLUMN IF NOT EXISTS department TEXT NOT NULL DEFAULT '';

-- One row per (type, scope): company rows keep department = '' and behave
-- exactly as before; 'Mining' rows override for that department only.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ppe_matrix_pkey') THEN
    ALTER TABLE ppe_matrix DROP CONSTRAINT ppe_matrix_pkey;
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ppe_matrix_type_scope_key') THEN
    ALTER TABLE ppe_matrix ADD CONSTRAINT ppe_matrix_type_scope_key PRIMARY KEY (ppe_type, department);
  END IF;
END $$;

-- Mining policy seed. Safe to re-run: existing rows are left untouched.
INSERT INTO ppe_matrix (ppe_type, interval_months, department) VALUES
    ('overall', 6, 'Mining'),       -- workbook: Overalls 6
    ('gumboots', 3, 'Mining'),      -- workbook: Gumboots 3
    ('safety_chain', 24, 'Mining'), -- workbook: Safety Chain 24 (separate from Chain & belt)
    ('helmet', 24, 'Mining'),       -- workbook: Hard Hat 24
    ('sinking_suit', 3, 'Mining'),  -- workbook: Sinking Suit 3 (separate from Rain Suit)
    ('harness', 24, 'Mining'),      -- owner-confirmed 2026-11-01 (blank in workbook)
    ('safety_shoes', 6, 'Mining'),  -- workbook: Safety Shoes 6
    ('jacket', 24, 'Mining')        -- owner-confirmed 2026-11-01 (blank in workbook)
ON CONFLICT (ppe_type, department) DO NOTHING;
