-- Optional working-day leave counting (exclude weekends + ZW public holidays).
ALTER TABLE leaves
  ADD COLUMN IF NOT EXISTS exclude_weekends_holidays boolean NOT NULL DEFAULT false;
