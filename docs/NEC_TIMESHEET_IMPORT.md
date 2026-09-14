# NEC timesheet import (in-app + CLI)

## In-app workflow (preferred)

**UI:** Timesheets → NEC tab → **Import scans**

**API:** `/api/nec-timesheet-import` (manager role for writes)

1. **Create job** — `POST /jobs` with `payroll_year`, `payroll_month` (NEC period = previous month 13th through payroll month 12th).
2. **Upload PDF(s)** — `POST /jobs/{id}/documents` (deduped by SHA-256).
3. **Attach review JSON** — `POST /jobs/{id}/review-json` (validated schema; period must match job).
4. **Preview** — `GET /jobs/{id}/preview` (proposed creates/updates, exceptions, missing NEC sheets, duplicate sheet groups).
5. **Apply** — `POST /jobs/{id}/apply?dry_run=true|false` (snapshot before write under `data/nec_import_snapshots/`).

Job files and history: `data/nec_import_jobs/{job_id}/` (gitignored).

### Extraction providers

Server env **`NEC_IMPORT_EXTRACTION_PROVIDER`** (default `manual_review_json`):

| Value | Behaviour |
|--------|-----------|
| `manual_review_json` | PDF stored for preview; operator uploads validated **review JSON** (same schema as Sep 2026 preparation). |
| `review_json_upload` | Use JSON already on the job (after upload). |

Future vision/OCR providers plug in via `app/nec_import/extraction/` without changing apply logic.

**Credentials** for any cloud vision API stay server-side only.

### Review JSON

- Not a raw POST payload to timesheets.
- `automatic_writes_allowed` must be **false**.
- Use `interpreted` values for apply; `source` for evidence; scan OT columns are reference-only.
- Mark duplicate scans with `disposition: "superseded"`.

Durable payroll rules: [NEC_TIMESHEET_RULES.md](./NEC_TIMESHEET_RULES.md).

## CLI (legacy / batch)

```bash
.venv/Scripts/python.exe scripts/nec_timesheet_import.py          # dry-run
.venv/Scripts/python.exe scripts/nec_timesheet_import.py --apply
```

Uses the same apply core as the API (`app/nec_import/apply_runner.py`). Default review path is local Codex output — override with `--review-json`.

## API completeness

`GET /api/timesheets` paginates past PostgREST’s 1000-row cap. Preview/apply use `fetch_all_pages`. Deploy backend with this fix for dense NEC periods.

## Configuration checklist

- Backend `.env`: Supabase service credentials (existing).
- Optional: `NEC_IMPORT_EXTRACTION_PROVIDER` when adding automated extraction.
- Render/production: ensure `data/` is writable for jobs and snapshots (or migrate jobs to Supabase storage — not required for v1).
