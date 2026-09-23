"""Add clearly identified example personnel to the Tools employee register.

The script is idempotent: employee numbers already present are left unchanged.
Run from backend/ with ``python scripts/seed_tools_personnel.py --apply``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.supabase_client import rows, supabase


EXAMPLE_PERSONNEL = [
    {"employee_number": "DEMO-ENG-001", "name": "Tariro Moyo", "department": "Engineering", "job_title": "Mechanical Fitter"},
    {"employee_number": "DEMO-ENG-002", "name": "Tawanda Ncube", "department": "Engineering", "job_title": "Electrician"},
    {"employee_number": "DEMO-ENG-003", "name": "Ruvimbo Dube", "department": "Engineering", "job_title": "Boilermaker"},
    {"employee_number": "DEMO-ENG-004", "name": "Farai Sibanda", "department": "Engineering", "job_title": "Instrumentation Technician"},
    {"employee_number": "DEMO-MIN-001", "name": "Kudzai Ndlovu", "department": "Mining", "job_title": "Shift Boss"},
    {"employee_number": "DEMO-MIN-002", "name": "Nyasha Chirwa", "department": "Mining", "job_title": "Development Miner"},
    {"employee_number": "DEMO-MIN-003", "name": "Blessing Mupfumi", "department": "Mining", "job_title": "LHD Operator"},
    {"employee_number": "DEMO-MIN-004", "name": "Rudo Maseko", "department": "Mining", "job_title": "Safety Representative"},
    {"employee_number": "DEMO-MTS-001", "name": "Tapiwa Zhou", "department": "Mine Technical Services", "job_title": "Mine Surveyor"},
    {"employee_number": "DEMO-MTS-002", "name": "Rutendo Gumbo", "department": "Mine Technical Services", "job_title": "Geologist"},
    {"employee_number": "DEMO-MTS-003", "name": "Simba Chigariro", "department": "Mine Technical Services", "job_title": "Planning Technician"},
    {"employee_number": "DEMO-MTS-004", "name": "Chipo Hove", "department": "Mine Technical Services", "job_title": "Ventilation Officer"},
    {"employee_number": "DEMO-IT-001", "name": "Tendai Mhlanga", "department": "IT", "job_title": "IT Manager"},
    {"employee_number": "DEMO-IT-002", "name": "Melissa Dube", "department": "IT", "job_title": "Systems Support Technician"},
    {"employee_number": "DEMO-IT-003", "name": "Panashe Moyo", "department": "IT", "job_title": "Network Technician"},
    {"employee_number": "DEMO-IT-004", "name": "Rumbidzai Ncube", "department": "IT", "job_title": "Service Desk Analyst"},
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Insert missing example personnel")
    args = parser.parse_args()

    existing = {
        row["employee_number"].casefold()
        for row in rows(supabase.table("tools_workspace_employees").select("employee_number").execute())
    }
    missing = [
        {**person, "active": True, "created_by": "Example personnel seed"}
        for person in EXAMPLE_PERSONNEL
        if person["employee_number"].casefold() not in existing
    ]
    if not args.apply:
        print(f"Dry run: {len(missing)} of {len(EXAMPLE_PERSONNEL)} example personnel would be added. Use --apply to save them.")
        return
    if missing:
        supabase.table("tools_workspace_employees").insert(missing).execute()
    print(f"Added {len(missing)} example personnel; {len(EXAMPLE_PERSONNEL) - len(missing)} already existed.")


if __name__ == "__main__":
    main()
