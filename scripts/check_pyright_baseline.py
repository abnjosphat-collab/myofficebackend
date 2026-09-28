"""Fail CI when the inherited Pyright error count increases."""

from __future__ import annotations

import json
import subprocess
import sys


MAX_ERRORS = 297


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--outputjson", "app"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(result.stdout)
        summary = report["summary"]
        error_count = int(summary["errorCount"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        return 2

    print(
        "Pyright baseline: "
        f"{error_count} errors across {summary['filesAnalyzed']} files "
        f"(maximum allowed: {MAX_ERRORS})."
    )
    if error_count > MAX_ERRORS:
        print("Type errors increased. Run `pyright app` and fix the regression.", file=sys.stderr)
        return 1
    if error_count < MAX_ERRORS:
        print(
            "Type debt improved. Lower MAX_ERRORS in "
            "scripts/check_pyright_baseline.py to preserve the gain."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
