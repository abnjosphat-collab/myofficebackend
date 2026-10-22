# Data integrity standard

The rules that keep whole categories of data bug from coming back. They exist because the same few mistakes were made one
module at a time: a rule checked only in the browser (so a second laptop recorded the same overtime twice and both were
approved), an approver name typed by the browser, an error swallowed into a 500, a record deleted without a trace. Each rule
below has one shared mechanism and a test that scans the code, so it also covers code that does not exist yet.

Read this before adding or changing a write endpoint.

## The rules

| # | Rule | The mechanism (reuse it, do not reinvent it) | The test that enforces it |
|---|---|---|---|
| 1 | **A rule is enforced on the server.** A browser warning is a courtesy, never the rule. Anything that must hold for the data (no overlap, no duplicate, a valid range) is checked where every writer meets it, and, where it is an exact duplicate, backed by a database unique index. | `app/overtime_conflicts.py`, `app/leave_conflicts.py`, `app/ppe_conflicts.py` (pure functions: rows in, a row or `None` out), applied by the router; unique-index files `supabase_migration_*_no_duplicates.sql` | `test_data_integrity_rules.py::test_every_range_record_has_an_overlap_rule_or_a_reason_it_does_not` |
| 2 | **Who and when come from the session, never from the browser.** Approver, rejecter, creator, sign-off: stamped on the server from the verified sign-in. The browser may send the drawn signature image, never a name or a time. | `current_user["name"]`, `app.audit.decision_stamp`, `app.audit.actor_name`; CrudRouter `before_create` / `before_update` hooks | `test_request_models_never_take_who_or_when_from_the_browser` |
| 3 | **Every change to payroll and people data is recorded.** One audit row per affected record: who, when, what changed (updates) or the whole row (creates and deletes, so a deleted record can always be put back). | The shared Supabase client records writes to `AUDITED_TABLES` automatically. Add a table to `AUDITED_TABLES` in `app/audit.py`; nothing else to remember. | `test_audit.py`, `test_the_payroll_and_people_tables_stay_audited`, `test_no_router_writes_an_audited_table_through_a_private_client` |
| 4 | **Errors keep their meaning.** A missing record is a 404, a rule refusal a 409/422, a database constraint refusal the matching 409/422, and only a real fault is a 500, with a reference and never the exception text. | `raise server_error(e, "Doing the thing")` from `app/errors.py`. Never write `raise HTTPException(500, ...)` inside a broad `except`. | `test_no_handler_builds_its_own_500_inside_a_broad_except`, `test_no_5xx_detail_includes_the_exception`, `test_server_error_mapping.py` |
| 5 | **Approved money records are not deleted.** Approved or paid overtime and approved leave cannot be deleted (it may already be in payroll): cancel (overtime) or reject (leave) instead, which keeps the history. An employee with records cannot be deleted: archive them. | The delete handlers refuse with 409; foreign keys back this in the database | `test_overtime_crud.py`, `test_leave_conflicts.py`, `test_employees_crud.py` |
| 6 | **Two people editing the same record do not silently overwrite each other.** The edit form sends back the `updated_at` it opened with; the server refuses a save made from a stale copy with a plain 409. | `app/concurrency.py` (`assert_unchanged`, `touch`); optional `expected_updated_at` on the update model | `test_overtime_crud.py`, `test_leave_conflicts.py`, `test_ppe_conflicts.py`, `test_timesheets_crud.py` |
| 7 | **Records refer to real records.** A text employee id must match an employee; renaming an employee id carries their history with it; names are saved tidy (one space between words). PPE issues stamp the person from the register and refuse unknown numbers. | Foreign keys `NOT VALID` then validated (`supabase_migration_integrity_constraints.sql`); `_follow_changed_employee` in `routers/employees.py`; `_stamp_person` in `routers/ppe.py`; `clean_name` validator | `test_employees_crud.py`, `test_ppe_crud.py` |
| 8 | **Free text stays free text.** Reasons, notes and descriptions are written by people and change all the time; do not force them into lists. Structure the fields that are compared, counted or matched (ids, dates, times, statuses, types). | (a decision, not code) | n/a |

## Adding a write endpoint: the checklist

1. Does the record claim a person's time or a date range? Apply a no-overlap rule (rule 1) or add the router to `OVERLAP_EXEMPT` with the reason.
2. Does anyone approve, sign or create it? Stamp it from the session (rule 2); leave the identity fields off the request model.
3. Is it payroll, leave, equipment, people or stock? Add the table to `AUDITED_TABLES` (rule 3).
4. Catch with `server_error`, not a hand-written 500 (rule 4). Raise your own 4xx for a rule refusal.
5. Can it be edited by two people? Add `expected_updated_at` (rule 6) and call `assert_unchanged` and `touch`.
6. Does it need a new column or table? Write a `supabase_migration_*.sql` file (never apply it yourself) and make the code work **before and after** it runs: `app.audit.has_columns(client, table, columns)` lets code use a new column only once it exists. Every new table needs `ENABLE ROW LEVEL SECURITY` in the same file (`test_migrations_rls.py`).
7. Add the tests: the refusal, the success, and the stale or duplicate case.

## Seeing what is already wrong

`scripts/data_integrity_report.py` (read-only) lists the duplicates, overlaps, orphaned records, approvals with no approver and stray spaces already in the data, using the same rules the server enforces. Write its `--out` file outside the repository: it contains names. `supabase_cleanup_names_and_spaces.sql` fixes the stray spaces. Nothing here deletes a record: the people who own payroll and leave decide what to keep.

## Running the SQL (owner), in this order

1. `supabase_migration_integrity_constraints.sql`: audit table, approver columns, `overtime.updated_at`, foreign keys and checks as `NOT VALID` (enforced for new and changed rows straight away). Then check `/api/health` shows `"audit": "ok"` after a change is made.
2. `supabase_cleanup_names_and_spaces.sql` (optional, changes names only).
3. Review the integrity report, resolve duplicates, then run `supabase_migration_overtime_no_duplicates.sql` and `supabase_migration_leaves_ppe_no_duplicates.sql` (each refuses to run while duplicates remain).
4. When the orphaned records are resolved, run the `VALIDATE CONSTRAINT` statements at the end of the first file.

The application works before and after each step. What the audit cannot see: changes made directly in the Supabase SQL editor.
