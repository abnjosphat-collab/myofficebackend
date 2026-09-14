"""Employee mine-number and name matching for NEC import."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


def normalize_code(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    c = str(raw).strip().upper().replace(" ", "")
    if not c:
        return None
    if c.startswith("C") and c[1:].isdigit():
        return "C" + str(int(c[1:])).zfill(4)
    if c.startswith("PP") and c[2:].isdigit():
        return "PP" + str(int(c[2:])).zfill(3)
    return c


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def sheet_human_code(sheet: dict) -> Optional[str]:
    emp = sheet.get("employee") or {}
    name = emp.get("name_raw") or emp.get("candidate_name") or ""
    low = name.lower()
    if "chidakwa" in low:
        return "PP058"
    raw = emp.get("mine_no_raw") or emp.get("candidate_employee_code")
    return normalize_code(raw)


def index_employees(rows: List[dict]) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    by_code: Dict[str, dict] = {}
    by_name: Dict[str, dict] = {}
    for e in rows:
        code = normalize_code(e.get("employee_id"))
        if code:
            by_code[code] = e
        by_name[normalize_name(f"{e.get('first_name', '')} {e.get('last_name', '')}")] = e
    return by_code, by_name


def resolve_employee(
    sheet: dict,
    by_code: Dict[str, dict],
    by_name: Dict[str, dict],
) -> Tuple[Optional[dict], str, List[dict]]:
    """Returns (employee row, evidence string, ambiguity candidates)."""
    emp = sheet.get("employee") or {}
    code = sheet_human_code(sheet)
    if code and code in by_code:
        return by_code[code], f"code:{code}", []
    name_key = normalize_name(emp.get("name_raw") or emp.get("candidate_name") or "")
    if name_key in by_name:
        return by_name[name_key], f"name:{name_key}", []
    # fuzzy: same normalized name prefix — flag ambiguity
    cands = [e for k, e in by_name.items() if name_key and (k.startswith(name_key[:8]) or name_key.startswith(k[:8]))]
    if len(cands) == 1:
        return cands[0], f"name_prefix:{name_key}", []
    return None, f"unresolved code={code} name={name_key}", cands[:5]
