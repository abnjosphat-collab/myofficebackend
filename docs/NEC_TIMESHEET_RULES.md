# NEC timesheet payroll rules (durable)

Scoped product rules confirmed for **NEC cycle** timesheets (13th–12th). Period-specific import data lives in import snapshots, not here.

## Cycle and ownership

- NEC payroll month runs **13th → 12th** (not calendar month).
- **Leaves** and **Overtime** are authoritative from their modules; scan columns are reconciliation only.
- **Night shift column** on paper = **night-shift allowance** (separate from normal hours): any **18:00–06:00** hours from a **rostered shift** (start/end on the row). **Automatic** — not a manual toggle. **Callout** / breakdown work at night uses `callout_overtime_hours`, not night allowance. Grid totals: `rosterNightAllowanceHours` → `nightAllowanceBonus` (see frontend `calcTotals.ts`).
- **Standby** = flat 8h per contiguous flagged run (calendar gaps start a new run).

## Normal hours

- Working days: preserve recorded normal hours (8 / 10 / 12 by role).
- **OFF** = 0 normal hours. **Blank ≠ OFF**.
- **Leave** = 8 normal hours per day via Leaves module (do not duplicate leave records from scans).

## Period totals (Actual / Reg / overtime)

Single source: **frontend repo** `app/timesheets/calcTotals.ts` (`calcEmployeeTotals`) — grid, summaries, and exports must all use it.

- **Actual** = uncapped sum of **normal** (`regular_hours`) for the period (includes leave-as-8h). Excludes double-time days (`weekend` / `holiday` worked → **2.0×** column only). Actual is a **reporting** figure; it stays full even when hours exceed 208.
- **Reg** = `min(Actual, 208)` — payable regular cap. **NEC exception:** if the employee has **no Absent** days in the period, **Reg defaults to 208** even when Actual is below 208 (shift pattern + normal **Off** rest days do not block this — only deliberate **Absent** does).
- **Normal excess** = `max(Actual − 208, 0)` — allocated to **1.5×**, not subtracted from Actual (always from Actual, not from the Reg floor).
- **1.5×** = normal excess **plus** module `overtime_hours` (additional OT from the Overtime module). Excel export: single **OT 1.5×** column with formula `MAX(0, Actual h − 208) + h1 + h2 + …` — one `+term` per approved 1.5× OT line (chronological); extend in the formula bar for manual 1.5× hours (no extra column). Do not persist excess on rows and add it again at render time.
- **2.0×** = double-time day totals plus `holiday_overtime_hours` on ordinary days; other categories stay separate.
- **Payable total** = Reg + 1.5× + 2.0× + night + standby + night allowance (Actual is not added again).

## Batch exception (Sep 2026 import only)

For **2026-08-13 … 2026-09-12** NEC grid reconciliation, unsigned/pending leave and overtime were included **without changing** their approval/signature status. Implemented in frontend `app/timesheets/necModuleBatch.ts` — **not** a global policy.

## API completeness

- Timesheet list and stats endpoints must **paginate** past PostgREST’s 1000-row default; an unpaginated fetch drops the **earliest dates** in a dense NEC period when ordered newest-first.

## Import tooling

- Review JSON: Codex `NEC-timesheets-*-review.json` (not an API payload).
- Apply: `scripts/nec_timesheet_import.py --apply` (snapshots under `data/nec_import_snapshots/` for rollback). See [NEC_TIMESHEET_IMPORT.md](./NEC_TIMESHEET_IMPORT.md).
