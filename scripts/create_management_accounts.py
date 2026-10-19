"""Create the management sign-in accounts: no invitation email, no password, role manager.

Usage (from backend/):
    .venv/Scripts/python.exe scripts/create_management_accounts.py            # create what is missing
    .venv/Scripts/python.exe scripts/create_management_accounts.py --dry-run  # show what it would do

Each account is created with its email already confirmed and NO password, so nothing is sent and nobody can sign in
until an administrator sets a password for them (Supabase dashboard > Authentication > Users > the person > Reset
password / set password, or the Admin page's reset). Idempotent: an email that already has a profile is left alone,
except that its role is raised to manager if it is lower. Uses SUPABASE_URL and SUPABASE_KEY (the service-role key)
from backend/.env. Never prints a key.
"""
from __future__ import annotations

import sys
import time

from app.supabase_client import supabase
from app.auth import ROLE_ORDER

ACCOUNTS = [
    {"email": "tafadzwa.matangira@dallaglio.co.zw", "full_name": "Tafadzwa Matangira"},
    {"email": "edson.mavhondo@dallaglio.co.zw", "full_name": "Edson Mavhondo"},
    {"email": "elias.mwazha@dallaglio.co.zw", "full_name": "Elias Mwazha"},
]
ROLE = "manager"


def _profile(email: str):
    rows = supabase.table("user_profiles").select("id,email,role,full_name").ilike("email", email).execute().data or []
    return rows[0] if rows else None


def main(dry_run: bool) -> int:
    failures = 0
    for account in ACCOUNTS:
        email, name = account["email"], account["full_name"]
        existing = _profile(email)
        if existing:
            have = existing.get("role") or "user"
            if ROLE_ORDER.index(have) < ROLE_ORDER.index(ROLE):
                print(f"{email}: exists as {have}; raising to {ROLE}" + (" (dry run)" if dry_run else ""))
                if not dry_run:
                    supabase.table("user_profiles").update({"role": ROLE, "full_name": existing.get("full_name") or name}).eq("id", existing["id"]).execute()
            else:
                print(f"{email}: exists as {have}; nothing to do")
            continue

        print(f"{email}: creating ({name}, {ROLE}, no invitation, no password)" + (" (dry run)" if dry_run else ""))
        if dry_run:
            continue
        try:
            created = supabase.auth.admin.create_user({"email": email, "email_confirm": True, "user_metadata": {"full_name": name}})
            user_id = str(created.user.id)
        except Exception as exc:  # report and carry on with the others
            print(f"  could not create {email}: {exc}")
            failures += 1
            continue

        # The on_auth_user_created trigger writes the profile (role 'user'); give it a moment, then set the role.
        profile = None
        for _ in range(10):
            profile = _profile(email)
            if profile:
                break
            time.sleep(0.5)
        if profile:
            supabase.table("user_profiles").update({"role": ROLE, "full_name": name}).eq("id", user_id).execute()
        else:
            supabase.table("user_profiles").insert({"id": user_id, "email": email, "full_name": name, "role": ROLE}).execute()
        print(f"  created {email} as {ROLE}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
