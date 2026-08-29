-- Make work_order_number unique so the racy max(existing)+1 generator can't mint the same
-- number twice on concurrent creates. With this index the DB REJECTS the second insert
-- (a clean error the app can retry) instead of silently storing a duplicate.
--
-- Run in the Supabase SQL editor. A UNIQUE index cannot be created while duplicates exist,
-- so run STEP 1 first. Only if it returns rows, run STEP 2. Then run STEP 3.

-- ── STEP 1 — find existing duplicates (read-only, safe). 0 rows → skip to STEP 3. ──
SELECT work_order_number, COUNT(*) AS copies
FROM work_orders
WHERE work_order_number IS NOT NULL
GROUP BY work_order_number
HAVING COUNT(*) > 1
ORDER BY copies DESC;

-- ── STEP 2 — ONLY IF STEP 1 RETURNED ROWS: de-duplicate before adding the index. ──
-- Keeps the earliest row's number as-is; suffixes the later copies (-dup2, -dup3, … by id).
-- Review STEP 1's output first — this rewrites those specific rows.
WITH ranked AS (
    SELECT id,
           work_order_number,
           ROW_NUMBER() OVER (PARTITION BY work_order_number ORDER BY id) AS rn
    FROM work_orders
    WHERE work_order_number IS NOT NULL
)
UPDATE work_orders w
SET work_order_number = w.work_order_number || '-dup' || ranked.rn
FROM ranked
WHERE w.id = ranked.id
  AND ranked.rn > 1;

-- ── STEP 3 — enforce uniqueness going forward. ──
-- Partial index: allows rows with a NULL number (legacy), blocks duplicate real values.
CREATE UNIQUE INDEX IF NOT EXISTS uq_work_orders_number
    ON work_orders (work_order_number)
    WHERE work_order_number IS NOT NULL;
