#!/usr/bin/env python3
"""CLI wrapper — prefer in-app /api/nec-timesheet-import for ongoing use."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.nec_import.apply_runner import run_import_from_review  # noqa: E402

PERIOD_START = "2026-08-13"
PERIOD_END = "2026-09-12"
DEFAULT_REVIEW = Path(
    r"C:\Users\Administrator\Documents\Codex\2026-09-12\compact\outputs"
    r"\NEC-timesheets-2026-08-13-to-2026-09-12-review.json"
)
SNAPSHOT_DIR = ROOT / "data" / "nec_import_snapshots"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-json", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    review = json.loads(args.review_json.read_text(encoding="utf-8"))
    report = run_import_from_review(
        review,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        apply=args.apply,
        snapshot_dir=SNAPSHOT_DIR,
    )
    out_report = SNAPSHOT_DIR / f"report_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(out_report), "stats": report["stats"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
