# PPE Budget (2027)

The Budget view on the PPE page plans next year's PPE spend from real data: the
register, the replacement matrix, issue history and the order list, plus 10 extra
people (5 confirmed, 5 provisional). Managers edit; everyone signed in reads.

## Server contract

- Table `ppe_budgets` (`supabase_migration_ppe_budgets_2026-10-30.sql`): one row
  per year — `year` PK, `settings` jsonb, `updated_by`, `updated_at`. Nothing is
  seeded; the defaults live in `app/routers/ppe_budget.py::default_settings`.
- `GET /api/ppe/budget/{year}`: saved settings (or the defaults), the same
  defaults under `defaults` for Reset, the year's calendar
  (`Mon–Fri minus app/zim_holidays.py`, weeks counted by Mondays, holiday names
  for the assumptions sheet), and `updated_at`.
- `PUT /api/ppe/budget/{year}` (manager): validates the settings (rates ≥ 0,
  known rule kinds, prices ≥ 0, months 1–12) and refuses with 409 when
  `expected_updated_at` no longer matches (`app/concurrency.py`).

## Frontend

- `app/ppe/budget/budgetCalc.ts`: the whole calculation, pure and tested
  (`budgetCalc.test.ts` holds the prompt's worked examples). Inputs: active
  people, PPE records, matrix, calendar, settings. Output: per line per month
  quantities and costs (with and without provisional starters), totals by
  month/item/section, gaps, sizes, premature-failure hints, actuals and the
  assumptions strings.
- `app/ppe/budget/BudgetView.tsx`: tiles, five charts (`Chart`/`ChartPanel`),
  the editable allocations table, month grid, starters/gaps/assumptions panels,
  Save with an unsaved-changes guard, Reset, and the downloads.
- `app/ppe/budget/budgetDownloads.ts`: XLSX (7 sheets, real SUM formulas;
  `budgetDownloads.test.ts` reads them back and confirms they evaluate to the
  screen's totals) and the A4-landscape PDF (tiles, two chart images, month
  grid, assumptions). Built on exceljs + jsPDF like the other exports.

## Rules worth remembering

- Consumable lines: qualifying people that month × the month's working days,
  weeks-starting-Monday, months or 1/12 of the year; quantities round up per
  month; buffer % per line; cost = qty × unit price (null price = quantities
  only, flagged "no price").
- Card lines: each holder's own due dates (last issue + matrix interval, rolled
  forward); interval 0 is never projected; unknown types are listed, never
  dropped; lost/damaged falls in January; gaps go in January when toggled.
- Starters count from their start month; the kit lands in the start month, then
  replaces on interval; kit defaults to matrix types at least half of staff hold.
