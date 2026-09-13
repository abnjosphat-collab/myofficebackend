# MyOffice — product and quality standard

**Audience:** engineers and agents working this codebase.  
**Status:** standing brief (Sep 2026). Update when product scope or quality bar changes.

## What we are building

Subscription ERP/MIS software for a **mine engineering department**. It centralises clerical work, coordination, documents, records, and operational information — reducing repetitive entry, scattered data, manual follow-ups, and the effort to understand departmental work.

**Maintenance management** (especially **work orders**) is a core workflow, not a side module.

Buyers may compare us with products like **eMaint X5** or **Sage 300** (different categories, same trust bar). We earn trust through dependable workflows, accurate data, maintainability, and a polished daily-use experience.

## Quality bar (concrete, not decorative)

High care means small defects matter: wrong totals, ambiguous controls, lost form input, inaccessible actions, inconsistent status labels, and silent failures undermine the whole product.

**Translate ambition into:**

- Complete delivery of requested scope — no partial “demo” behaviour in production paths.
- Simplest implementation that correctly solves the problem.
- Verification before claiming done (see [ENGINEERING_PRINCIPLES.md](./ENGINEERING_PRINCIPLES.md)).
- Fixes at the cause — not styling hacks, swallowed errors, unsafe casts, or disabled checks.

Reference craftsmanship (principles only, not stack copy): **Ozech** (`PRINCIPLES.md`, `ENGINEERING.md`) and **School/Meridian Hill** (agent memory discipline, architecture docs). Visual style stays **MyOffice** — see frontend repo `components/shared/design-system/README.md`.

## UX and visual design

- Calm, modern, precise interface for professional daily use.
- Use the **shared design system** (`CenterModal`, `PageHero`, tokens, Phosphor via `theme`) — extend shared components; do not hand-roll per-page glass/gradients.
- Discoverability before hover: primary actions, navigation, and dismiss controls need obvious affordances (e.g. adequate hit targets on close buttons).
- Deliberate **loading, empty, error, validation, success, and permission** states. Failures must not look like empty data (see [ENGINEERING_STANDARDS.md](./ENGINEERING_STANDARDS.md)).
- Keyboard focus, contrast, labels, and control sizes are part of polish — not optional extras.

## Work orders

Treat work orders as a **critical journey**. Before changing status rules or labels, read [WORK_ORDERS.md](./WORK_ORDERS.md) and trace backend + UI.

Users should quickly answer: what needs attention, which asset/location, who owns it, priority/status/due date, what is overdue or blocked, what remains to close, and where history/documents live.

## Dashboards and metrics

Define metrics precisely (numerator, denominator, period, statuses included, reopened/cancelled handling). Dashboard totals must reconcile with drill-down under the same filters and permissions. Distinguish **zero**, **missing**, **loading**, **stale**, and **failed** — never fabricate KPIs.

## Where code standards live

| Topic | Location |
|--------|----------|
| Frontend wiring (`calcX.ts`, `useXData`, apiClient) | frontend repo `docs/ENGINEERING_STANDARDS.md` |
| Backend wiring (CrudRouter, errors, tests) | [ENGINEERING_STANDARDS.md](./ENGINEERING_STANDARDS.md) |
| UI tokens and components | frontend repo `components/shared/design-system/README.md` |
| Environment, verification commands, git | MyOffice workspace `.claude/skills/myoffice-conventions/SKILL.md` |
| Work order map | [WORK_ORDERS.md](./WORK_ORDERS.md) |
| NEC timesheet payroll rules | [NEC_TIMESHEET_RULES.md](./NEC_TIMESHEET_RULES.md) |
| Engineering process | [ENGINEERING_PRINCIPLES.md](./ENGINEERING_PRINCIPLES.md) |

## Reference projects (optional read)

Paths on this machine — if unavailable, use this doc and the repo.

- Ozech: `…/studio/ozech/backend/docs/PRINCIPLES.md`, `ENGINEERING.md`, `ARCHITECTURE.md`
- School: `…/Cursor/school/AGENTS.md`, `MEMORY.md`, `.cursor/rules/meridian-hill.mdc`, `backend/docs/ARCHITECTURE.md`

Do not copy their auth, deployment, or domain rules into MyOffice without an explicit decision.
