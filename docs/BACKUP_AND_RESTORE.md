# Backup and restore (MyOffice)

One page stating where the data can come back from. Written 22 Oct 2026 for
audit finding F-22. Every fact below is currently UNKNOWN — the owner was
asked on 22 Oct 2026 and could not answer. To close this page, supply the
four facts; update it, do not rewrite it.

## Backups

- **Where backups live:** UNKNOWN (Supabase project backups may exist on the
  plan; nobody has confirmed which project, which plan, or what is covered).
- **Retention:** UNKNOWN (how many days back a restore can reach).
- **Who can restore:** UNKNOWN (which person holds the dashboard access and
  has done it before).
- **Last restore test:** UNKNOWN (no record of a restore ever being tested).

Until these are known, the honest statement is: recovery from a data-loss
event is unplanned and untested. The re-run guard on
`supabase_migration_maintenance_schedules.sql` (F-06) stops one specific way
of losing the schedules table, but it is not a backup.

## What "closed" looks like

1. The four facts above, each with a date it was verified.
2. A restore test against a non-production project at least once, with its
   date recorded here.
3. The retention window written down so "can we get back Tuesday's rows?"
   has an answer without opening the dashboard.

## Error reporting

- Backend: `sentry-sdk` is an optional dependency but is not configured —
  turning it on needs a Sentry DSN, which is the owner's decision (a secret
  with a cost/account attached). Not done unasked.
- Frontend: no error reporting beyond the `tools_workspace_errors` table and
  error pages. Adding a reporting dependency is also the owner's decision.
