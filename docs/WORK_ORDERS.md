# Work orders — implementation map

Work orders are a **priority workflow** for engineering managers. Do not invent or silently change status semantics without explicit product approval.

## Authoritative status values (current backend)

From `app/routers/maintenance.py` stats and filters:

| Status | Notes |
|--------|--------|
| `pending` | Default on create (`WorkOrderCreate.status`) |
| `in-progress` | Hyphenated string in DB |
| `completed` | Used for overdue logic exclusion |
| `on-hold` | Hyphenated string in DB |

Overdue calculation treats rows with `due_date` and `status != 'completed'`. Any new status must be wired through stats, filters, and UI consistently.

## Code locations

| Layer | Path |
|--------|------|
| API | `app/routers/maintenance.py` — CRUD, stats, dashboard aggregates |
| UI page | frontend repo `app/maintenance/page.tsx` |
| Modals / forms | frontend `components/maintenance/CreateWorkOrderModal.tsx`, `WorkOrderDetailModal.tsx`, `formFields.tsx`, `analytics.tsx` |
| Related ops | Breakdowns, requisitions, spares, equipment pages may link or reference maintenance context — grep before assuming isolation |

## Audit trail, row version and comments (slice 1 of the Maintenance rebuild)

Needs `supabase_migration_maintenance_audit.sql` (applied by the owner, not by code). Rehearse it with `scripts/test_maintenance_audit_sql.sh`.

| Endpoint | Behaviour |
|---|---|
| `PATCH /work-orders/{id}` | Optional body field `version`. If it is sent and is not the current row version the write is refused with **409** and `detail = {code: "version_conflict", message, current: <row>}`; nothing is written. Without `version` the call behaves as before. The database bumps `version` on every update. |
| `GET /work-orders/{id}/events` | The audit trail, newest first. A failed read is an error, never an empty list. |
| `GET /work-orders/{id}/comments` | Comments, oldest first. |
| `POST /work-orders/{id}/comments` | `user` role or above. Body `{body}`, 1 to 4000 characters, not blank. |

Create, update, delete and comment each append one `maintenance_events` row (`app/maintenance_events.py`). The row is written after the change; if the append fails the change stands and the failure is logged at ERROR. Signature images are not copied into the change list.

## Registers, the leave rule and tools (slice 3 of the Maintenance rebuild)

Code: `app/maintenance_registers.py`. Migration: `supabase_migration_maintenance_tools.sql` (apply after the audit migration; rehearse with `scripts/test_maintenance_tools_sql.sh`).

| Endpoint | Notes |
|---|---|
| `GET /api/maintenance/registers/leave?on=YYYY-MM-DD` | People on approved leave on a day (today by default): name, leave type, start and end date. |
| `GET /api/maintenance/registers/tools` | Read-only feed of the Tools & Equipment register behind the normal MyOffice sign-in. Archived tools are left out; `status` (available, issued, overdue, attention) and `inspection_due` are derived as the Tools workspace derives them. There is no write route. |
| `GET /work-orders/{id}/tools` | Tools the job needs. |
| `PUT /work-orders/{id}/tools` | `user` role or above. Replaces the list (max 50); a repeated register number is kept once; a null register number is a free-text tool. Audited. A failed save restores the previous list. |

**The leave rule.** Creating a work order, or changing `allocated_to`, `responsible_foreman` or `authorising_foreman`, is refused with 409 `{code: "person_on_leave", message, people}` when the name matches someone on approved leave today (case and spacing ignored). Only names being set or changed are checked, so an old record can still be saved. The requester is not checked, and neither are `artisan_name` and `foreman_name`: they record who did the work and signed it off, which can be true of someone who has since gone on leave. If the leave register cannot be read the answer is 503, never "nobody is on leave". Same for the tools feed: a failed read is a 503, never an empty list.

## Journey checklist (when touching WOs)

- [ ] Create / edit / assign / schedule fields persist correctly (`exclude_unset` on PATCH — no null-clear regressions).
- [ ] List filters, sort, and search preserve context when opening and returning from detail.
- [ ] Status and priority visible at a glance; overdue distinguishable from on-track.
- [ ] Completion vs formal closure: preserve any business distinction already in UI copy and fields — do not merge without approval.
- [ ] Documents, notes, spares, and history reachable from the record where the app already supports them.
- [ ] Errors surface to the user; empty list ≠ failed load.
- [ ] Stats on homepage/maintenance dashboard reconcile with filtered lists.

## Metrics

If adding reporting (closure rate, backlog age, on-time completion), document numerator/denominator/period/status set in the PR or doc **before** implementing. Reuse `get_work_order_stats()` patterns or extend them — do not duplicate conflicting counts.
