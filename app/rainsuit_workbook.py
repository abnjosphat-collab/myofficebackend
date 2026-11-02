# backend/app/rainsuit_workbook.py - Raincoat allocation reader and size reconciliation (Batch 2).
#
# Reads the two-sheet Engineering raincoat workbook (Raincoat Allocation / Size
# Summary) into staged size requirements plus an explicit exception queue.
# Nothing here writes to the database, creates employees, fabricates PPE
# issues, or guesses sizes: the rows are requirements dated 30/10/2026, and
# unknown sizes, flagged handwriting, and unmatched names stay visible for
# reviewed import later.
#
# People matching rule: normalized surname-token match only (single-letter
# initials never match). Zero hits is unmatched, several is ambiguous; a
# single hit is a candidate for explicit owner confirmation, never automatic.
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

SOURCE_REF = 'raincoat-2026-10'
REQUIREMENT_DATE = '30/10/2026'

CANONICAL_SIZES = ('S', 'M', 'L', 'XL', 'XXL', 'XXXL')
SIZE_SUMMARY_ORDER = ['S', 'M', 'L', 'XL', 'XXL', 'XXXL']

ALLOC_HEADER_ROW = 4
ALLOC_FIRST_BODY_ROW = 5
ALLOC_COLUMN_COUNT = 7

_SIZE_TOKEN = re.compile(r'\b(XXXL|XXL|XL|L|M|S)\b', re.IGNORECASE)


def canonical_size(raw: Any) -> Optional[str]:
    """Source size label to canonical size. Unknown or blank stays None: never guessed."""
    if raw is None:
        return None
    match = _SIZE_TOKEN.search(str(raw))
    return match.group(1).upper() if match else None


def _text(value: Any) -> str:
    return str(value or '').strip()


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass
class RequirementRow:
    """One allocation body row: a staged size requirement, never an issue."""
    row: int
    name: str
    department: str
    size_source: str
    size_canonical: Optional[str]
    quantity: int
    reference: str
    notes: str

    @property
    def is_issue(self) -> bool:
        return False

    @property
    def issue_date(self) -> None:
        return None


@dataclass
class AllocationReading:
    rows: list[RequirementRow] = field(default_factory=list)


@dataclass
class SizeSummary:
    counts: dict[str, int] = field(default_factory=dict)
    unknown: int = 0
    total_quantity: int = 0
    people: int = 0

    def by_size(self, size: str) -> int:
        return self.counts.get(size, 0)


def parse_allocation(alloc_ws) -> AllocationReading:
    """Staged requirements; blank-size rows keep their source value verbatim."""
    reading = AllocationReading()
    for r in range(ALLOC_FIRST_BODY_ROW, alloc_ws.max_row + 1):
        values = [alloc_ws.cell(r, c).value for c in range(1, ALLOC_COLUMN_COUNT + 1)]
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
            continue
        if values[2] is None or _text(values[2]) == '':
            continue
        # The TOTAL RAINCOATS footer is an aggregate, never a person.
        if _text(values[2]).upper().startswith('TOTAL'):
            continue
        size_raw = values[3]
        reading.rows.append(RequirementRow(
            row=r,
            name=_text(values[2]),
            department=_text(values[1]),
            size_source='' if size_raw is None else _text(size_raw),
            size_canonical=canonical_size(size_raw),
            quantity=_int_or_zero(values[4]) or 1,
            reference=_text(values[5]),
            notes=_text(values[6]),
        ))
    return reading


def summarize(reading: AllocationReading) -> SizeSummary:
    """Canonical size counts plus unknown; people count never stands in for quantity."""
    summary = SizeSummary(people=len(reading.rows))
    for item in reading.rows:
        summary.total_quantity += item.quantity
        if item.size_canonical is None:
            summary.unknown += 1
        else:
            summary.counts[item.size_canonical] = summary.counts.get(item.size_canonical, 0) + item.quantity
    return summary


def parse_size_summary(summary_ws) -> list[str]:
    """Source summary size labels in sheet order."""
    return [_text(summary_ws.cell(r, 1).value)
            for r in range(4, 10) if _text(summary_ws.cell(r, 1).value)]


def read_workbook(path: str) -> dict[str, Any]:
    """Parse both sheets from a file path. Used by rehearsal tooling, not by the API."""
    from openpyxl import load_workbook
    workbook = load_workbook(path, data_only=True)
    try:
        reading = parse_allocation(workbook['Raincoat Allocation'])
        return {'rows': reading.rows, 'summary': summarize(reading)}
    finally:
        workbook.close()


def _surname_tokens(name: str) -> set[str]:
    return {token.upper() for token in re.split(r'[\s.]+', name or '') if len(token.strip()) > 1}


@dataclass
class PeopleReconciliation:
    """Outcome of matching source names against the register (no writes)."""
    matched: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)


def reconcile_people(rows: list[dict[str, Any]], register: list[dict[str, Any]]) -> PeopleReconciliation:
    """Match when exactly one register surname equals a source name token.
    Zero hits is unmatched, several is ambiguous — never a silent pick."""
    by_surname: dict[str, list[dict[str, Any]]] = {}
    for emp in register:
        key = (emp.get('last_name') or '').strip().upper()
        if key:
            by_surname.setdefault(key, []).append(emp)
    out = PeopleReconciliation()
    for item in rows:
        hits: list[dict[str, Any]] = []
        for token in _surname_tokens(item.get('name', '')):
            hits.extend(by_surname.get(token, []))
        unique: dict[str, dict[str, Any]] = {h['id']: h for h in hits if h.get('id')}
        if len(unique) == 1:
            emp = next(iter(unique.values()))
            out.matched.append({'source_name': item.get('name'), 'row': item.get('row'),
                                'register_id': emp.get('id'),
                                'register_department': emp.get('department'),
                                'mine_number': emp.get('employee_id')})
        elif len(unique) > 1:
            out.conflicts.append({'source_name': item.get('name'), 'row': item.get('row'),
                                  'reason': 'ambiguous_surname',
                                  'register_ids': sorted(unique)})
        else:
            out.unmatched.append({'source_name': item.get('name'), 'row': item.get('row'),
                                  'reason': 'no_token_match'})
    return out
