# NEC timesheet import

Review JSON from transcription (not an API payload) is applied via:

```bash
.venv/Scripts/python.exe scripts/nec_timesheet_import.py          # dry-run
.venv/Scripts/python.exe scripts/nec_timesheet_import.py --apply
```

Snapshots and reports are written under `data/nec_import_snapshots/` (gitignored JSON). Durable payroll rules: workspace `docs/NEC_TIMESHEET_RULES.md`.
