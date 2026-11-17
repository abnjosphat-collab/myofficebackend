# Overtime reliability - 17 November 2026

Local candidate on `fix/overtime-reliability-20261117`; not committed, pushed or deployed. Separate linked checkouts preserve unrelated Training work in the original checkouts.

The owner saw create/delete failures although writes eventually committed, and slow refreshes. A bounded production read confirmed the wrong pending 30 November record is absent and retained in Recently deleted, deleted at 05:26 UTC on 17 November. This investigation made no operational writes. Private records and credentials are not documentation fixtures.

Changes: move overtime list/detail/delete/approval/edit/lieu and analysis blocking work off the event loop; exclude signature images in SQL list projections; stable creation-time/ID ordering; reconcile uncertain inserts by the exact server-generated timestamp for that attempt without replaying INSERT; remove sensitive payload logging. Existing authorization, audit/trash, concurrency, payroll and overlap rules are preserved.

Frontend confirmed responses update the register locally without a whole-register read. Older in-flight reads cannot undo those writes, and unmounted lists cannot start new retry chains. Lost delete responses are reconciled by one detail read. Uncertain saves preserve input and say `Save not confirmed`; definite refusals remain refusals.

Verification: full backend **3,013 passed**, coverage **92.80%**; one inherited Starlette/httpx deprecation warning. Pyright **189 errors**, below inherited ceiling **196**, unchanged. Frontend overtime/dialog/delete tests **146 passed**, refresh hook **17 passed**. The clean backend run supersedes earlier disk-exhausted invalid runs; no full frontend coverage pass is claimed. SQL projection fields were checked against the hosted schema with a bounded read.

The normal frontend build could not fetch Google Fonts. A local verification build reuses actual cached MyOffice font bytes through Next.js's test-response hook, stored outside Git. That exposed pre-existing unsupported named exports in 12 page modules and the budget route. The separate compatibility change forwards pages to unchanged same-folder `PageContent.tsx` files; tests import helpers there. Implementations match HEAD after normalizing Git line endings. `bookPath` moved to its own module. No screen layout/workflow was redesigned. The documentation ledger follows the forwarding modules.

Final frontend evidence is in its `docs/OVERTIME_RELIABILITY_2026-11-17.md`. Render service discovery returned null, so ref `d976c505` has not been correlated with production logs. Public version checks timed out; that is not proof of a provider outage.

Final local frontend build and all built-app overtime fixture browser checks passed; desktop/mobile screenshots inspected. Compatibility timeout/worker recheck plus shared date defaults passed 23 tests. No schema migration required. Obtain owner release approval under `frontend/docs/ENGINEERING_PROCESS.md`, then backend first and frontend preview/production; confirm deployed revisions, provider font download/build and signed-in outcomes. No production test payroll records. Rollback is a scoped code revert; no operational-data rollback is required.
