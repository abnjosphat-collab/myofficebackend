-- Migration: extend spares table with multi-category support, notes, and supplementary columns
-- Run this in the Supabase SQL editor

-- Add categories array for multi-category tagging
ALTER TABLE spares ADD COLUMN IF NOT EXISTS categories text[] DEFAULT '{}';

-- Add notes column (may already exist in some deployments, safe to re-run)
ALTER TABLE spares ADD COLUMN IF NOT EXISTS notes text;

-- Add unit_of_measure (standard unit label)
ALTER TABLE spares ADD COLUMN IF NOT EXISTS unit_of_measure text DEFAULT 'UN';

-- Add lead_time_days (procurement lead time)
ALTER TABLE spares ADD COLUMN IF NOT EXISTS lead_time_days integer DEFAULT 0;

-- Add last_ordered_date
ALTER TABLE spares ADD COLUMN IF NOT EXISTS last_ordered_date date;

-- Index for category array containment queries (GIN index required for @> operator)
CREATE INDEX IF NOT EXISTS idx_spares_categories ON spares USING GIN (categories);

-- Index for notes full-text-ish search (ilike queries benefit from standard index)
CREATE INDEX IF NOT EXISTS idx_spares_notes ON spares (notes);

-- Index for stock_code (likely already exists, safe to re-run)
CREATE INDEX IF NOT EXISTS idx_spares_stock_code ON spares (stock_code);

-- Backfill: copy existing single category into categories array where categories is empty
UPDATE spares
SET categories = ARRAY[category]
WHERE category IS NOT NULL
  AND category != ''
  AND (categories IS NULL OR array_length(categories, 1) IS NULL);
