-- Adds an optional "Spares Used" reference/cost log to overtime requests — a list of
-- { name, part_number, quantity, unit_price, total_cost } objects, same shape and same
-- scope as breakdowns.spares_used / work_orders.spares_used: it never touches the
-- Spares module's stock (current_quantity stays a Stores-department function). Run
-- once in the Supabase SQL editor.

ALTER TABLE public.overtime ADD COLUMN IF NOT EXISTS spares_used JSONB NOT NULL DEFAULT '[]'::jsonb;
