# backend/app/tool_set228_workbook.py - SET-228 workbook reader and recipient reconciliation (Batch 1).
#
# Reads the three-sheet 228-piece hand-tools workbook (Set Overview /
# Set Contents / Set Issue Log) into staged set-type lines plus an explicit
# exception queue. Nothing here writes to the database, creates employees, or
# guesses corrections: the advertised-228 vs printed-227 gap, the printed
# duplicate line, and every ambiguous/unmatched recipient stay visible for
# reviewed import later.
#
# Recipient matching rule: normalized exact surname only. The owner supplied
# surnames, not mine numbers; zero hits is unmatched (owner supplies the mine
# number), several hits is ambiguous. A single hit is a candidate for explicit
# owner confirmation, never an automatic link.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

SET_CODE = 'SET-228'

# The owner's corrected recipient list: one Bhunu only, 18 intended recipients.
INTENDED_RECIPIENTS = [
    'Muronda', 'Bhunu', 'Hnakie', 'Simango', 'Hamadziripi', 'Nemai',
    'Tongogara', 'Diva', 'Denford', 'Phili', 'Mutova', 'Chirenje',
    'Midzi', 'Ncube Herbert', 'Zulu', 'Mapamba', 'Zaronga', 'Frank',
]

# Set Overview label rows (column A) read by parse_overview.
OVERVIEW_FIRST_ROW = 3
OVERVIEW_LAST_ROW = 11
# Set Contents header row; body rows below it carry the set code in column A.
CONTENTS_HEADER_ROW = 3
CONTENTS_FIRST_BODY_ROW = 4
CONTENTS_COLUMN_COUNT = 7
# Set Issue Log header row; columns A-G must hold values for a row to count
# as structurally complete (H, remarks, stays optional).
LOG_HEADER_ROW = 4
LOG_FIRST_BODY_ROW = 5
LOG_COLUMN_COUNT = 8
LOG_REQUIRED_COLS = 7


def _text(value: Any) -> str:
    return str(value or '').strip()


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return int(value)


@dataclass
class SetContentLine:
    """One Set Contents body row: a staged set-type line with its source row."""
    row: int
    category: str
    name: str
    size: str
    qty: int
    unit: str
    notes: str


@dataclass
class ContentsReading:
    lines: list[SetContentLine] = field(default_factory=list)
    # Same name+size printed on more than one row (kept as printed, flagged).
    duplicates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def total_qty(self) -> int:
        return sum(line.qty for line in self.lines)


@dataclass
class IssueLogReading:
    complete: list[dict[str, Any]] = field(default_factory=list)
    incomplete_rows: list[int] = field(default_factory=list)
    blank_template_rows: int = 0

    @property
    def incomplete(self) -> list[int]:
        return self.incomplete_rows


def parse_overview(overview_ws) -> dict[str, Any]:
    """Label/value pairs from the Set Overview block, returned unadjusted."""
    labels = {
        'Set code': 'set_code',
        'Set description': 'description',
        'Unit of issue': 'unit_of_issue',
        'Advertised pieces': 'advertised_pieces',
        'Pieces itemized on printed photo': 'printed_pieces',
        'Unreconciled advertised pieces': 'unreconciled',
        'Distinct item variants': 'distinct_variants',
        'Verification': 'verification',
        'Source': 'source',
    }
    info: dict[str, Any] = {}
    for r in range(OVERVIEW_FIRST_ROW, OVERVIEW_LAST_ROW + 1):
        key = labels.get(_text(overview_ws.cell(r, 1).value))
        if not key:
            continue
        value = overview_ws.cell(r, 2).value
        info[key] = _int_or_none(value) if isinstance(value, (int, float)) or key in (
            'advertised_pieces', 'printed_pieces', 'unreconciled', 'distinct_variants') else _text(value)
    for cell, key in (('A13', 'issuing_rule'), ('A15', 'gap_note')):
        info[key] = _text(overview_ws[cell].value)
    return info


def parse_contents(contents_ws) -> ContentsReading:
    """Staged set-type lines; duplicate name+size rows flagged, never merged."""
    reading = ContentsReading()
    seen: dict[tuple[str, str], list[int]] = {}
    for r in range(CONTENTS_FIRST_BODY_ROW, contents_ws.max_row + 1):
        values = [contents_ws.cell(r, c).value for c in range(1, CONTENTS_COLUMN_COUNT + 1)]
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
            continue
        # Data rows always carry the set code; the TOTAL formula row does not.
        if _text(values[0]) != SET_CODE:
            continue
        line = SetContentLine(
            row=r,
            category=_text(values[1]),
            name=_text(values[2]),
            size=_text(values[3]),
            qty=int(values[4]) if values[4] is not None else 0,
            unit=_text(values[5]),
            notes=_text(values[6]),
        )
        reading.lines.append(line)
        seen.setdefault((line.name.upper(), line.size.upper()), []).append(r)
    for (name, size), rows in seen.items():
        if len(rows) > 1:
            original = next(line for line in reading.lines if line.row == rows[0])
            reading.duplicates.append({'name': original.name, 'size': original.size, 'rows': rows})
    return reading


def parse_issue_log(log_ws) -> IssueLogReading:
    """Complete rows (A-G all present); partial rows quarantined by number."""
    result = IssueLogReading()
    for r in range(LOG_FIRST_BODY_ROW, log_ws.max_row + 1):
        values = [log_ws.cell(r, c).value for c in range(1, LOG_COLUMN_COUNT + 1)]
        # Column C carries a prefilled SET-228 on template rows; blankness is
        # decided by every other column.
        rest = [v for c, v in enumerate(values, start=1) if c != 3]
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in rest):
            result.blank_template_rows += 1
            continue
        if all(values[c] is not None and _text(values[c]) != '' for c in range(LOG_REQUIRED_COLS)):
            result.complete.append({'row': r, 'cells': {c + 1: values[c] for c in range(LOG_COLUMN_COUNT)}})
        else:
            result.incomplete_rows.append(r)
    return result


def read_workbook(path: str) -> dict[str, Any]:
    """Parse all three sheets from a file path. Used by rehearsal tooling, not by the API."""
    from openpyxl import load_workbook
    workbook = load_workbook(path, data_only=False)
    try:
        return {
            'overview': parse_overview(workbook['Set Overview']),
            'contents': parse_contents(workbook['Set Contents']),
            'issue_log': parse_issue_log(workbook['Set Issue Log']),
        }
    finally:
        workbook.close()


def normalize_surname(raw: Any) -> str:
    return str(raw or '').strip().upper()


@dataclass
class RecipientReconciliation:
    """Outcome of matching source surnames against the register (no writes)."""
    matched: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)


def reconcile_recipients(names: list[str], register: list[dict[str, Any]]) -> RecipientReconciliation:
    """Match by normalized exact surname only. Zero hits is unmatched, several
    is ambiguous; a same-surname difference is never an automatic link."""
    by_surname: dict[str, list[dict[str, Any]]] = {}
    for emp in register:
        key = normalize_surname(emp.get('last_name'))
        if key:
            by_surname.setdefault(key, []).append(emp)
    out = RecipientReconciliation()
    for name in names:
        holders = by_surname.get(normalize_surname(name), [])
        if not holders:
            out.unmatched.append({'source_name': name})
        elif len(holders) > 1:
            out.conflicts.append({'source_name': name, 'reason': 'ambiguous_surname',
                                  'register_ids': [h.get('id') for h in holders]})
        else:
            emp = holders[0]
            out.matched.append({'source_name': name, 'register_id': emp.get('id'),
                                'register_department': emp.get('department'),
                                'mine_number': emp.get('employee_id')})
    return out
