# MyOffice — engineering principles

Companion to [PRODUCT.md](./PRODUCT.md). Applies to **both** frontend and backend repos.

## Inspect before implementing

This product already exists. For each request:

1. Read project skills/rules and relevant standards (see PRODUCT.md table).
2. Trace the workflow: UI → state/hooks → API → router/service → DB → permissions → tests.
3. Note what to reuse vs. where the bug actually lives.
4. For shared components, grep representative consumers before changing behaviour.
5. Plan with **observable acceptance criteria**, then implement.

Do **not** replace the stack, rebuild the app, or broad-refactor unrelated areas because the quality bar is high.

## Architecture boundaries

- **UI:** presentation and interaction; no authoritative business rules in `page.tsx` blobs — extract `calcX.ts` / hooks per frontend standards.
- **API:** transport and validation; keep multi-step rules in dedicated modules or router logic with tests.
- **Data:** Supabase/Postgres as source of truth; migrations explicit and verified on dev/test DB.
- **Shared UI:** frontend `components/shared/design-system/` — one visual language.
- **Reuse:** `CrudRouter`, `db_helpers`, `apiClient`, `useLookups`, `CenterModal` before new one-offs.

Principle from Ozech: predictable layers (model / query / service / router / schemas) — adapt to FastAPI + Next.js layout, do not force alien folder trees.

## Verification is part of the work

Before calling a change done:

| Check | Frontend | Backend |
|--------|-----------|---------|
| Types | `npx tsc --noEmit` | — |
| Tests | `npx vitest run` (targeted + calc tests) | `pytest -q` |
| Build | `npm run build` when UI/routes change | — |
| Manual | Critical paths in browser when feasible | — |

Report honestly: **implemented**, **automated tests run**, **manually checked**, **not verified**.

Add tests for calculations, transitions, permissions, and failure paths — not only happy paths. A 200 response or green build does not prove a user can complete the task.

## Persistent guidance hygiene

- One authoritative home per topic; link instead of duplicating the full brief.
- Update workspace `memory/MEMORY.md` when runbooks, decisions, or limitations change.
- Record **specific** lessons (what broke, rule adopted, file/test to guard it).
- No secrets in memory, rules, or docs.

## Feature delivery skill

For substantial features, use MyOffice workspace `.cursor/skills/feature-delivery/SKILL.md` checklist.
