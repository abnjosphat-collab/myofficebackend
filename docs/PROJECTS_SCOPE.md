# Projects scope — batch log and endpoint inventory

General "account limited to one section" feature; first account: a Projects-scoped
user. Server enforces, browser only makes it obvious. Out-of-scope single
records answer 404. Unscoped accounts get exactly today's responses.

## Method

Route walk: booted `main.app`, enumerated `app.openapi()["paths"]` → **319
paths, 491 method+path pairs**. Each router file mapped to its Supabase tables
(`.table()`, `TABLE =`, `CrudRouter("…")`, model fields). Every endpoint below
was read against its router; link fields were confirmed in the models, not
guessed from names.

## Link rules

- **(P) through person** — row links an employee; visible iff the person is in
  the viewer's section. Person link fields: `employee_id` (register id text),
  `employee_db_id`, `recipient_id` (stock issues, optional — see Q6).
- **(S) direct section** — row carries the organisational section itself
  (`section`, `section_choice`, `mine_section`, `department_section`).
- **(C) needs column** — Batch 4 SQL adds nullable `section`; NULL means "not
  Projects".
- **(D) inherits parent** — sub-resource; takes the parent's section.
- **(X) exempt** — no scoped rows (health/version, own signature, shared
  catalogues, manager-only surfaces). Each exemption names its reason.

## Batch 2 — people modules (rule P; employees carry section directly)

| Table | Endpoints (method path) | Notes |
|---|---|---|
| employees | GET POST /api/employees; GET /api/employees/search/{query}; POST /api/employees/bulk-normalize; GET PUT DELETE /api/employees/{employee_id} | Row `section` is the anchor. Create forces viewer's section; edit cannot move a person out. Exempt: /health, /health/status |
| leaves | GET POST /api/leaves; GET /api/leaves/stats/summary; GET PATCH DELETE /api/leaves/{leave_id}; POST /api/leaves/bulk-status | Through `employee_id` |
| overtime | GET POST /api/overtime; POST /api/overtime/analyze; POST /api/overtime/bulk-edit; POST /api/overtime/bulk-status; GET PATCH DELETE /api/overtime/{overtime_id}; POST /api/overtime/{overtime_id}/take-as-leave | Through person. Reads `equipment(name)` only to cross-match reason text — non-row use (X). |
| timesheets | GET POST /api/timesheets; GET /api/timesheets/stats/summary; GET PATCH DELETE /api/timesheets/{entry_id} | Through person — NOTE `employee_id` is the INT db id here, TEXT register id in leaves/overtime. Roster/grid/export all read these |
| nec import jobs | GET /api/nec-timesheet-import/config (X: provider settings); GET POST /api/nec-timesheet-import/jobs; GET /api/nec-timesheet-import/jobs/{job_id}; POST .../apply; POST .../cancel; POST .../documents; GET .../documents/{doc_id}/pages/{page_index}/preview; POST .../extract; GET .../preview; POST .../review-json | Jobs live on DISK (data/nec_import_jobs), not a table — the guard cannot see them; apply writes timesheet rows → must stamp + match Projects people only |
| artisan_timesheets | GET POST /api/artisan-timesheets; GET PATCH DELETE /api/artisan-timesheets/{timesheet_id} | Through `employee_db_id`/`employee_id`. Reads leaves for the no-work-on-leave guard (covered: the artisan's own leave) |
| ppe_records | GET POST /api/ppe; GET /api/ppe/employee/{employee_id}; GET PUT /api/ppe/matrix; POST /api/ppe/matrix/apply-all; POST /api/ppe/matrix/{ppe_type}/apply; GET /api/ppe/stats/summary; GET PATCH DELETE /api/ppe/{record_id}; GET POST /api/maintenance/ppe (second door, same table) | Through person; rows also stamp `mine_section` from the register on create/move |
| ppe_order_list | GET POST DELETE /api/ppe-order-list(/); POST /api/ppe-order-list/remove; PATCH /api/ppe-order-list/{record_id} | Through `employee_id` |
| stock_issues | GET POST /api/issues; GET /api/issues/stats/summary; DELETE PATCH /api/issues/{item_id} | Through `recipient_id` (optional) — see Q6 |
| training_certifications — GAP, not in PLAN | GET POST /api/training(/); GET /api/training/alerts/expiring; GET /api/training/employee/{employee_id}; GET /api/training/reports/compliance_rate; GET /api/training/reports/due_refreshers; GET /api/training/stats/summary; GET PUT DELETE /api/training/{record_id} | Through `employee_id` (+`department` copy). Recommend Batch 2 |
| competency_matrix — GAP | GET POST /api/competency(/); DELETE PATCH /api/competency/{item_id} | Through `employee_id`. Recommend Batch 2 |
| standby_schedules — GAP | GET POST /api/standby(/); GET PUT DELETE /api/standby/{assignment_id} | Through `employee_id`. Recommend Batch 2 |
| duty_roster — GAP | GET POST /api/duty-roster; GET PATCH DELETE /api/duty-roster/{entry_id} | Through `employee_id`. Recommend Batch 2 |
| standby_rotations — GAP | GET POST /api/standby-rotations(/); DELETE PATCH /api/standby-rotations/{item_id}; GET /api/standby-rotations/{rotation_id} | HAS `section` already + members by `employee_id`. Recommend Batch 2/3 |
| duty_rotations — GAP | GET POST /api/duty-rotations(/); DELETE PATCH /api/duty-rotations/{item_id}; GET /api/duty-rotations/{rotation_id} | Members by `employee_id`, `department`, NO section — needs column or member rule (Q7) |
| rotation_covers — GAP | GET POST /api/rotation-covers; GET PATCH DELETE /api/rotation-covers/{cover_id} | Holder + stand-in by employee — visible iff both/any in section (Q7) |
| shift_handovers — GAP | GET POST /api/shift-handovers; GET /api/shift-handovers/stats/summary; GET PATCH DELETE /api/shift-handovers/{handover_id} | HAS `section`. Recommend Batch 3 |
| job_cards — GAP | GET POST /api/job-cards; GET /api/job-cards/export/excel; GET PUT DELETE /api/job-cards/{job_card_id} | HAS `section` (+`assigned_to`). Recommend Batch 3 |

## Batch 3 — tables with a section field (rule S; meaning confirmed per table in-batch)

| Table | Endpoints | Notes |
|---|---|---|
| nearmiss_reports | GET POST /api/nearmiss; GET /api/nearmiss/stats/overview; GET PATCH DELETE /api/nearmiss/{report_id} | `section` |
| sheq_inspections | GET POST /api/sheq(/); GET /api/sheq/stats/overview; GET PATCH DELETE /api/sheq/{inspection_id} | `section` |
| sheq_findings (D) | GET POST /api/sheq/{inspection_id}/findings; DELETE PATCH /api/sheq/findings/{finding_id} | Inherit inspection's section |
| work_stoppage_reports | GET POST /api/work-stoppage(/); GET /api/work-stoppage/stats/overview; GET /api/work-stoppage/suggestions/departments; GET /api/work-stoppage/suggestions/inspectors; GET PATCH DELETE /api/work-stoppage/{report_id} | `section`. Suggestions are distincts over rows → scoped distincts |
| pto_reports (+pto_action_plan, D) | GET POST /api/pto(/); GET /api/pto/export/excel; GET /api/pto/stats/overview; GET /api/pto/suggestions/observers; GET PATCH DELETE /api/pto/{report_id} | `section` BUT create pattern is `^(Mechanical\|Electrical)$` — must widen for Projects or creates refuse |
| requisitions (+requisition_items, D) | GET POST /api/requisitions; GET /api/requisitions/daily-total/{date}; GET /api/requisitions/stats/summary; GET PATCH DELETE /api/requisitions/{requisition_id} | `section` |
| safety_complaints — GAP | GET POST /api/safety-complaints; GET /api/safety-complaints/stats/overview; GET PATCH DELETE /api/safety-complaints/{complaint_id} | HAS `section` (default "General") — confirm it is org section (Q3) |
| lube_schedules — GAP | GET POST /api/lubrication(/); GET POST /api/lubrication/records; DELETE PATCH /api/lubrication/{s_id} | HAS `section` — confirm org vs plant area (Q3) |

## Batch 4 — tables needing a section column (rule C; NULL = not Projects)

vfl_reports and pachedu_reports already carry `section_choice`
(`^(Mechanical|Electrical)$`) — they move to rule S with pattern widening, no
new column. corrective_actions ride nested inside stoppage payloads (no own
endpoints) and are stamped from the parent.

| Table | Endpoints | Notes |
|---|---|---|
| equipment | GET POST /api/equipment; GET PATCH DELETE /api/equipment/{equipment_id} | Exempt: /health/status |
| work_orders | GET POST /api/maintenance/work-orders; GET /api/maintenance/work-orders/allocated/{allocated_to}; GET /api/maintenance/work-orders/stats/summary; GET POST /api/maintenance/work-orders/{work_order_id}/comments; GET /api/maintenance/work-orders/{work_order_id}/events; GET PATCH DELETE /api/maintenance/work-orders/{work_order_id}; GET /api/maintenance/dashboard/stats | `to_section`/`from_section` are routing, not ownership — new column per PLAN. Comments/events (D). Schedules POST /api/schedules/generate stamps from schedule (Q8) |
| breakdowns | GET POST /api/breakdowns(/); GET /api/breakdowns/analytics/heatmap; GET /api/breakdowns/dashboard/overview; GET /api/breakdowns/get-breakdowns; GET PATCH DELETE /api/breakdowns/{breakdown_id} | Exempt: /health/check |
| notices | GET POST /api/notices; GET /api/notices/stats/summary; POST /api/notices/upload-attachment; GET PATCH DELETE /api/notices/{notice_id} | |
| sop_documents (+sop_revisions, D) | GET POST /api/sops(/); GET /api/sops/archived; GET PATCH /api/sops/{sop_id}; POST /api/sops/{sop_id}/archive; POST /api/sops/{sop_id}/restore | |
| services (+attachments, signatures, D) | GET POST /api/services(/); POST /api/services/ocr; PUT DELETE /api/services/{service_id}; GET POST /api/services/{service_id}/attachments; DELETE .../attachments/{attachment_id}; GET /api/services/{service_id}/signatures; PUT .../signatures/{stage} | No GET single; PUT/DELETE on missing id must 404 when out of scope |
| daily_reports | GET POST /api/daily-reports; GET /api/daily-reports/export/excel; GET /api/daily-reports/export/pdf/{report_id}; GET /api/daily-reports/stats/summary; GET /api/daily-reports/trends/equipment; GET /api/daily-reports/trends/summary; GET PATCH DELETE /api/daily-reports/{report_id} | Exports included |
| pachedu_reports (S, moved) | GET POST /api/pachedu; GET /api/pachedu/stats/overview; GET /api/pachedu/suggestions/departments; GET PATCH DELETE /api/pachedu/{report_id} | `section_choice` exists; widen pattern |
| vfl_reports (S, moved) + vfl_action_plan (D) | GET POST /api/vfl(/); GET /api/vfl/stats/overview; GET /api/vfl/suggestions/observers; GET PATCH DELETE /api/vfl/{report_id} | `section_choice` + `department_section`; widen pattern |
| maintenance_schedules (+runs, D) | GET POST /api/schedules; POST /api/schedules/generate; PATCH DELETE /api/schedules/{schedule_id}; GET /api/schedules/{schedule_id}/runs | |
| availabilities + availability_records | GET /api/availabilities; GET /api/availabilities/history/{equipment_id}; GET /api/availabilities/stats; GET POST /api/availability-records; GET /api/availability-records/from-breakdowns; PUT DELETE /api/availability-records/{record_id} | Rows keyed by `equipment_id` — recommend inherit equipment's section instead of a new column (Q5) |
| corrective_actions | (nested in stoppage payloads; no own endpoints) | Stamp from parent report |
| compliance — GAP | GET POST /api/compliance(/); GET PATCH DELETE /api/compliance/{c_id} | Optional `equipment_id` — inherit or out (Q9) |
| condition_monitoring readings — GAP | GET POST /api/condition-monitoring(/); DELETE PATCH /api/condition-monitoring/{item_id} | Optional `equipment_id` — inherit or out (Q9) |
| compressors, compressor_readings, daily_entries, service_records, maintenance_schedule — GAP | All /api/compressors/* (dashboard, analytics, export, readings, services, report) | Own telemetry tables, no person/section link. Recommend out of scope (Q9) |

## Batch 5 — documents and summaries

Documents: POST /api/documents/bulk; POST /api/documents/folders; POST
/api/documents/folders/seed-ams; PUT DELETE /api/documents/folders/{folder_id};
GET /api/documents/overview; POST /api/documents/reindex; GET
/api/documents/search; POST /api/documents/upload; PUT DELETE
/api/documents/{doc_id}; GET /api/documents/{doc_id}/activity. Scoped users see
only the Projects subtree, upload there, search only there. `seed-ams` and
`reindex` need a scope decision (Q10).

Summaries: every /stats/summary, /stats/overview, /dashboard/*,
/analytics/heatmap, /trends/*, /reports/* endpoint listed above counts only
in-scope rows. No backend home/dashboard aggregator and no backend
notifications feed exist (verified: no such router) — home figures, the
engineering report/dashboard and the bell aggregate client-side from the
module endpoints, so scoping the endpoints scopes them. Usage analytics
(/api/usage/events) stays manager-only with a clear notice.

## Out of scope (unchanged)

Spares/inventory catalogues, quotations, tasks-events (manager-gated),
audit/event-log (manager-only), Admin/Manage user (extends: scope setting),
lookup-lists (shared catalogue), AI analysis, health/version, own signature,
sign-in cache behaviour (keyed, Batch 1). Tools workspace (Batch 6, owner
chooses). Photos upload/delete: storage attachments with no table link found
(Q9). Drivers, production, contractors, failure-modes: no
person/equipment/section link found (Q9).

## Questions for the owner (Batch 0 → before Batch 1)

- Q1 GAP tables: training, competency, standby, duty roster/rotations,
  rotation covers, handovers, job cards — scope them (recommended: yes, same
  batches as their rule)?
- Q2 safety_complaints.section (default "General") and lube_schedules.section:
  organisational section (scope) or something else (out)?
- Q3 PTO/VFL/Pachedu `section`/`section_choice` patterns only allow
  Mechanical|Electrical — widen to Projects (recommended) or separate field?
- Q4 work_orders: new `section` column per PLAN (to/from stay routing)?
- Q5 availabilities: inherit equipment's section (recommended, no column) or
  new column per PLAN?
- Q6 stock_issues: `recipient_id` optional — scope by id, fall back to
  recipient_name match, or leave unscoped?
- Q7 duty_rotations (no section, members by person) and rotation_covers
  (holder + stand-in): member rule, new column, or out?
- Q8 schedules/generate: generated work orders inherit the schedule's section
  (recommended: yes)?
- Q9 Unlinked/small: compliance, condition readings, compressors, photos,
  drivers, production, contractors, failure-modes — shared readable, or scope
  some?
- Q10 documents seed-ams + reindex under a scoped account: refuse, or run
  Projects-only?

## Batch 1 — done (branch projects-scope-1, both repos)

Plumbing + obvious UI; no per-module filtering yet (Batches 2–5).

- `app/scope.py`: TABLES registry (section/person/members/inherits/subtree)
  + viewer_scope/scope_person_ids/apply_list_scope/row_visible/filter_rows/
  assert_row_visible/stamp_create. Unknown table + scoped viewer fails closed.
- `app/auth.py`: get_current_user fetches `permissions.scope` via sibling RPC
  `get_user_scope_by_id`; RPC failure (incl. missing function pre-migration)
  is a loud 503, never silent "unscoped". The 30s sign-in cache stores the
  scope with the user; set/clear calls invalidate_user (next request bites).
- `app/cache.py`: build_key() takes scope (unscoped keys byte-identical);
  @cached folds scope from current_user automatically.
- `PATCH /api/admin/users/{id}/scope`: set/clear, same self/super_admin
  guard as other admin user actions, audited via the normal write path.
- `supabase_migration_user_scope.sql` (file only, NOT run): creates the RPC.
  Must be applied before the Batch 1 backend is deployed.
- Frontend: `scope` on useAuth() (from profile.permissions, no new fetch);
  quiet section Tag by the account menu; Admin Manage-user section-limit
  field + Section column; personnel page is the pilot (pinned section,
  hidden Section filter, "No {scope} people yet", fixed section on the form).
  Browser cache hygiene: shared lookup caches (employees/equipment/spares)
  are dropped + generation-guarded when the signed-in account changes, so a
  cached slice can never serve another account; creates pin section=scope
  in the payload (edits keep the row's section; the server has final say).
- Decisions: per-module frontend adoption rides with its enforcement batch
  (Batches 2–5), not all in Batch 1 — filter-hiding without enforcement
  would mislead, and each batch stays verifiable end-to-end. "Projects" is
  not in the frontend SECTION_ORDER list, so the admin scope field is
  free-text with canonical suggestions, not a closed select.
- Suite: test_projects_scope.py 9 red / 10 green (registry test went green
  as designed; 8 behaviour + 1 guard red until enforcement lands).

## Batch 2 — done (branch projects-scope-2, both repos)

- People modules enforced: employees, leaves, overtime, NEC + artisan
  timesheets, PPE (records + order list), stock issues (Q6: recipient id,
  NULL falls back to name), training, competency, standby/duty rosters and
  rotations, rotation covers. CrudRouter gained `scope_table=` hooks;
  hand-rolled routers got list/single/create/patch guards. Out-of-scope
  singles 404; duplicate/overlap checks see visible rows only; NEC preview
  sliced, apply refused 403 (partial apply would corrupt payroll).
- Members rules filter in Python (handfuls of rows); empty IN sets spell
  match-nothing for PostgREST. Q7: rotation visibility is ANY-member.
- Frontend: scope-aware headers + empties on 9 pages, PPE section pin,
  NEC apply replaced by a whole-mine notice. Pickers ride the scoped
  shared hook; no form changes needed.
- Suite: test_projects_scope.py 4 red (nearmiss, equipment, findings,
  guard); scope files 120 green; pyright 194/196.

## Batch 3 — done (branch projects-scope-3, both repos)

- Direct-section tables enforced (9 routers): near miss, work stoppage,
  SHEQ inspections + findings (nested + standalone), PTO, requisitions,
  plus the PLAN-unnamed direct-section orphans — handovers, job cards
  (CrudRouter `scope_table=` + hand-rolled single/patch asserts),
  lubrication, safety complaints. Findings/actions/items inherit their
  parent; requisition numbers/line numbers/daily totals see the slice.
- Section validation patterns widened to Projects (near miss, stoppage,
  PTO); no DB CHECK constrains these columns. Stats slice in Python and
  gain the scope key (unscoped shapes byte-identical).
- Registry corrections: lube_schedules is direct-section (it carries its
  own column; the Batch 1 inherits entry would have hidden every row
  until Batch 4); lube_records added as inherits-via-schedule (also added
  to the Batch 0 inventory). Q2: complaints keep General/blank open —
  lists filter in Python (open_values can't be one predicate) with
  limit/offset on the slice.
- `distinct_suggestions()` takes the user and scopes values (stoppage
  departments/inspectors, PTO observers; pachedu/VFL call sites stay
  unscoped until Batch 4). Patch rule is stay-or-scope: the section may
  stay as it is or become the viewer's scope, never anything else (so a
  General complaint stays editable).
- Frontend: 'Projects' joins every section union/options list (incl. the
  lowercase SHEQ list — stored values must match the scope exactly);
  forms pin the section to a disabled field, filters hide + pin,
  near-miss section tiles hide when scoped. SHEQ dashboard rides scoped
  fetches (VFL/Pachedu panels complete in Batch 4).
- Suite: test_projects_scope.py 2 red (equipment + guard, both Batch 4);
  test_scope_batch3.py 63 green; pyright 192/196.

## Batch 4 — done (branch projects-scope-4, both repos)

- SQL (file only, NOT run): `supabase_migration_projects_scope_section_columns.sql`
  adds a nullable `section` column to equipment, work_orders, breakdowns,
  notices, sop_documents, services, daily_reports, maintenance_schedules.
  Existing rows stay NULL = "not Projects" (untriaged, invisible to scoped
  accounts); no backfill. PostgREST needs ~30s to pick up the columns.
- Enforced (13 routers): equipment, maintenance/work_orders (+events,
  comments, allocated pickers, stats), breakdowns (dashboard stays open via
  the optional-user pattern — anonymous sees whole-mine KPIs), notices,
  SOPs, services (+attachments inherit the service), daily reports (upsert
  grain is date x section for scoped), schedules (runs guarded; generated
  work orders inherit the schedule's section), pachedu + VFL (validation
  patterns widened to Projects), availability + compliance register +
  condition monitoring (inherit equipment; unlinked compliance rows hidden
  from scoped). Registry: `compliance` corrected to `compliance_register`.
- Regressions caught and fixed in-batch: breakdowns dashboard briefly
  required auth (reopened with get_current_user_optional); services
  attachment delete briefly 404'd for unscoped missing rows (guard now
  scoped-only, idempotent delete preserved).
- Harness: `in_` gains the same PostgREST int/str coercion as `eq`
  (TEXT equipment_id links), plus gte/lte/gt/lt range operators.
- Frontend: pachedu + VFL fully adopted (Projects joins the section type,
  options, filter hide + pin, form pin to a disabled field, scoped empty;
  ReportDialog exported for tests). Headers + honest `No {scope} ... yet`
  empties on equipment, maintenance (+schedules), breakdowns, noticeboard,
  SOP library, services, both availability pages, compliance register,
  condition monitoring (noticeboard/SOP alias the auth scope to
  `viewerScope` — both pages already own a `scope` local). Daily reports
  has no frontend consumer, so nothing to adopt. Migration ledger
  regenerated (pachedu/VFL test counts).
- Suite: test_projects_scope.py 18/19 — equipment green, guard red ONLY on
  the Batch 5 documents tables; test_scope_batch4a/b.py 39 + 42 green;
  full backend 2514 passed; endpoint auth 68 passed; pyright 184/196.
  Frontend: tsc/eslint clean, vitest 1952 passed, docs:check clean.
