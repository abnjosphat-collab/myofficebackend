# NEC timesheet import

Review JSON from transcription (not an API payload) is applied via:

```bash
.venv/Scripts/python.exe scripts/nec_timesheet_import.py          # dry-run
.venv/Scripts/python.exe scripts/nec_timesheet_import.py --apply
```

Snapshots and reports are written under `data/nec_import_snapshots/` (gitignored JSON). Durable payroll rules: workspace `docs/NEC_TIMESHEET_RULES.md`.

**API note:** `GET /api/timesheets` paginates past PostgREST’s 1000-row cap (stable `date`, `id` order). The grid needs the deployed backend with this fix — local frontend against an old Render build will still look truncated.
