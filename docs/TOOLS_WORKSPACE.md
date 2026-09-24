# Tools & Equipment backend contract

`app/routers/tools_workspace.py` is the API for the standalone `/tools` workspace. `supabase_migration_tools_workspace.sql` contains the complete schema and atomic change RPCs. Incremental production changes remain in separately named migrations.

## Business rules

- Signed-in viewers can read tools, employees, history and notifications.
- Administrators manage accounts, the register, imports, evidence, archive/restore, undo/redo, feedback and analytics. Administrators cannot record custody movements.
- Only Issuers can issue, transfer, extend and return equipment. Each Issuer has one assigned department; the API rejects movements for a tool or employee outside that department.
- New self-registered accounts are Viewers. The first account bootstraps as Admin, and an Admin may promote a Viewer to Issuer with a department or revoke that access later.
- Cross-account analytics and the feedback inbox are administrator-only. Usage and errors may still be captured from any authenticated account.
- Issue, transfer, extend and return operations retain authenticated actor, server time, custody and history.
- Command writes remain atomic and idempotent.
- `issued` is the stored open-loan state. API responses derive `overdue` whenever `custody.expected_return_at` is earlier than current UTC time. This keeps lists and notifications correct without relying on a scheduler.
- Overdue notification keys include the expected-return timestamp. Extending a deadline creates a new alert identity when that deadline later passes.
- Notification reads are stored per account in `tools_workspace_notification_reads`.
- A condition requiring inspection produces an `attention` alert.
- Evidence and audio feedback stay in private buckets and are returned with temporary signed URLs.
- Flexible equipment specifications are stored in `tools_workspace_equipment.specifications`; custody can retain multiple `assigned_equipment` names and a free-text work location.

## Verification

Run `python -m pytest tests/test_tools_workspace_router.py -q` after changing this contract. Tests cover Admin/Issuer/Viewer boundaries, departmental custody, role elevation, custody history, durable undo/redo, derived overdue state, account-scoped notification acknowledgement, analytics, import and attachment metadata.

Example personnel can be inserted idempotently with `python scripts/seed_tools_personnel.py --apply`. Their employee numbers start with `DEMO-` so they are easy to identify and replace before production use.
