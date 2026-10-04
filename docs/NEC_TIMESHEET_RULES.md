# NEC timesheet payroll rules (durable)

Scoped product rules confirmed for **NEC cycle** timesheets (13th–12th). Period-specific import data lives in import snapshots, not here.

## Cycle and ownership

- NEC payroll month runs **13th → 12th** (not calendar month).
- **Leaves** and **Overtime** are authoritative from their modules; scan columns are reconciliation only.
- **Night shift column** on paper = **night-shift allowance** (separate from normal hours): any **18:00–06:00** hours from a **rostered shift** (start/end on the row). **Automatic** — not a manual toggle. **Callout** / breakdown work at night uses `callout_overtime_hours`, not night allowance. Grid totals: `rosterNightAllowanceHours` → `nightAllowanceBonus` (see frontend `calcTotals.ts`).
- **Standby** = flat 8h per contiguous flagged run (calendar gaps start a new run).

## Normal hours

### Confirmed holiday and standby import rules (2 October 2026)

- Worked-holiday daily cells show the normal shift once: **10h** (or **12h** for a 12h shift). The additional holiday premium appears only in the **2.0x total column**, not as a repeated extra-hours line in the daily cell. Preserve module-sourced `holiday_overtime_hours` for calculation and audit details; do not add those hours to the displayed normal shift.
- Worked holidays retain **normal shift hours plus separate 2.0x overtime from the Overtime module**. Usual 8h/day workers receive **10 normal hours** on a worked holiday; 12h shift workers receive **12 normal hours**. The user designates **15 September 2026** for the October NEC import.
- Import worked-holiday base rows with `status: work` so normal hours remain in Actual. The current `holiday` status routes regular hours only to 2.0x. Set holiday overtime from deduplicated module records; do not add the same premium twice. Non-worked paid holidays and leave do not become worked holidays.
- **8 standby allowance hours per distinct standby period**, regardless of its duration; **two periods = 16h**. Use `standby*`/comments as period evidence. Preserve explicit boundaries even when periods touch; do not pay 8h per flagged day.
- Match against the current live Employees register, including temporary mine codes. Timesheets use numeric database IDs. Never invent mine numbers or create employees from uncertain scans.
- Blank normal cells remain unresolved. Leaves/OT come from their modules; missing leave is a reconciliation issue. NEC includes all non-rejected module records, including pending and unsigned records, without changing source approval/signature state.
- Missing night allowance is completed from explicit rostered shifts: **18:00-06:00 = 12h**, **03:00-06:00 = 3h**, separate from normal hours and counted once.

- Working days: preserve recorded normal hours (8 / 10 / 12 by role).
- **OFF** = 0 normal hours. **Blank ≠ OFF**.
- **Leave** = 8 normal hours per day via Leaves module (do not duplicate leave records from scans).

## Period totals (Actual / Reg / overtime)

Single source: **frontend repo** `app/timesheets/calcTotals.ts` (`calcEmployeeTotals`) — grid, summaries, and exports must all use it.

- **Actual** = uncapped sum of **normal** (`regular_hours`) for the period (includes leave-as-8h). Excludes double-time days (`weekend` / `holiday` worked → **2.0×** column only). Actual is a **reporting** figure; it stays full even when hours exceed 208.
- **Reg** = `min(Actual, 208)` — payable regular cap. **NEC exception:** if the employee has **no Absent** days in the period, **Reg defaults to 208** even when Actual is below 208 (shift pattern + normal **Off** rest days do not block this — only deliberate **Absent** does).
- **Normal excess** = `max(Actual − 208, 0)` — allocated to **1.5×**, not subtracted from Actual (always from Actual, not from the Reg floor).
- **1.5×** = normal excess **plus** module `overtime_hours` (additional OT from the Overtime module). Excel export: single **OT 1.5×** column with formula `MAX(0, Actual h − 208) + h1 + h2 + …` — one `+term` per eligible non-rejected 1.5× OT line (chronological); extend in the formula bar for manual 1.5× hours (no extra column). Do not persist excess on rows and add it again at render time.
- **2.0×** = double-time day totals plus `holiday_overtime_hours` on ordinary days; other categories stay separate.
- **Payable total** = Reg + 1.5× + 2.0× + night + standby + night allowance (Actual is not added again).

## Source approval policy (confirmed 2 October 2026)

For **2026-08-13 … 2026-09-12** NEC grid reconciliation, unsigned/pending leave and overtime were included **without changing** their approval/signature status. Implemented in frontend `app/timesheets/necModuleBatch.ts` — **not** a global policy.

## API completeness

- Timesheet list and stats endpoints must **paginate** past PostgREST’s 1000-row default; an unpaginated fetch drops the **earliest dates** in a dense NEC period when ordered newest-first.

## Import tooling

- Review JSON: Codex `NEC-timesheets-*-review.json` (not an API payload).
- Apply: `scripts/nec_timesheet_import.py --apply` (snapshots under `data/nec_import_snapshots/` for rollback). See [NEC_TIMESHEET_IMPORT.md](./NEC_TIMESHEET_IMPORT.md).
