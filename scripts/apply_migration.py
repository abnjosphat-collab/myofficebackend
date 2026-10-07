"""Apply SQL migration files to the Supabase project via the Management API.

Usage (from backend/):
    .venv/Scripts/python.exe scripts/apply_migration.py <file.sql> [<file.sql> ...]

Needs SUPABASE_URL (for the project ref) and SUPABASE_ACCESS_TOKEN (a personal
access token with database scopes) in backend/.env. Each file is sent as one
query; any failure stops the run with a non-zero exit and the API's error.
The token is never printed.
"""

import json
import os
import sys
import urllib.request
import urllib.error

from dotenv import load_dotenv


def project_ref(url: str) -> str:
    host = url.split("https://", 1)[1].split("/", 1)[0]
    return host.split(".", 1)[0]


def run_query(ref: str, token: str, sql: str) -> tuple[int, str]:
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{ref}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode()[:500]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]


def main(paths: list[str]) -> int:
    load_dotenv()
    url = os.getenv("SUPABASE_URL", "")
    token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
    if not url or not token:
        print("SUPABASE_URL and SUPABASE_ACCESS_TOKEN must both be set in backend/.env")
        return 2
    ref = project_ref(url)
    for path in paths:
        with open(path) as f:
            sql = f.read()
        if not sql.strip():
            print(f"{path}: empty file, skipped")
            continue
        status, body = run_query(ref, token, sql)
        print(f"{path}: HTTP {status}")
        if status not in (200, 201):
            print(f"  {body}")
            return 1
    print(f"Applied {len(paths)} file(s) to project {ref}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
