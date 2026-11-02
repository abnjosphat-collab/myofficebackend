# backend/app/mining_ppe_workbook.py - Mining PPE workbook reader and reconciliation (SOLUTION batch 1).
#
# Reads the four-sheet Mining PPE workbook (PPE Tracker / Summary / Settings /
# Issue Log) into typed staged rows plus an explicit exception queue. Nothing
# here writes to the database, creates employees, or guesses corrections: every
# anomaly (invalid/future/old dates, non-formula derived cells, unknown
# sections, incomplete log rows) is quarantined for reviewed import later.
#
# Matching rule: normalized exact mine number only (stripped, uppercased).
# Name similarity never links two records; a same-number name difference is a
# conflict for explicit resolution, never an automatic merge.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

# Header row of the PPE Tracker; people are rows below it with a name in column B.
TRACKER_HEADER_ROW = 5
TRACKER_FIRST_BODY_ROW = 6
TRACKER_MINE_NO_COL = 1
TRACKER_NAME_COL = 2
TRACKER_COST_CENTRE_COL = 3
# First column of each last-issued/next-due/status triplet (eight items).
TRACKER_TRIPLET_COLS = (4, 7, 10, 13, 16, 19, 22, 25)
# Settings rows holding the eight item names (column A) and their intervals (column B).
SETTINGS_FIRST_ITEM_ROW = 5
SETTINGS_ITEM_COUNT = 8
SETTINGS_WARNING_CELL = 'B15'
# Issue Log body starts at this row; columns A-H, of which A-G must hold values
# for a row to count as structurally complete.
LOG_FIRST_BODY_ROW = 5
LOG_COLUMN_COUNT = 8
LOG_REQUIRED_COLS = 7
# Dates before this year are flagged for review, never silently year-corrected.
REVIEW_YEAR_CUTOFF = 2020


def normalize_mine_number(raw: Any) -> str:
    """Source match key: stripped, uppercased. Never infers a leading letter."""
    return str(raw or '').strip().upper()


def normalize_name(raw: Any) -> str:
    return str(raw or '').strip()


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


@dataclass
class TrackerPerson:
    """One Tracker body row: identity plus raw per-item last-issued values."""
    row: int
    mine_number: str
    name: str
    cost_centre: str
    # item name -> {'issued_raw': the cell value, 'cell': 'D6'-style reference}
    items: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class DateException:
    kind: str  # 'invalid' | 'future' | 'pre_2020'
    cell: str
    raw: str


@dataclass
class WorkbookReading:
    people: list[TrackerPerson] = field(default_factory=list)
    policy: dict[str, Optional[int]] = field(default_factory=dict)
    warning_days: Optional[int] = None
    log_complete: list[dict[str, Any]] = field(default_factory=list)
    log_incomplete_rows: list[int] = field(default_factory=list)
    date_exceptions: list[DateException] = field(default_factory=list)
    derived_nonformula_cells: list[str] = field(default_factory=list)
    stray_cells: list[dict[str, Any]] = field(default_factory=list)


def _item_names(settings_ws) -> list[str]:
    return [str(settings_ws.cell(r, 1).value or '').strip()
            for r in range(SETTINGS_FIRST_ITEM_ROW, SETTINGS_FIRST_ITEM_ROW + SETTINGS_ITEM_COUNT)]


def parse_policy(settings_ws) -> tuple[dict[str, Optional[int]], Optional[int]]:
    """Item name -> interval months (None stays None: unknown is not zero)."""
    policy: dict[str, Optional[int]] = {}
    for r in range(SETTINGS_FIRST_ITEM_ROW, SETTINGS_FIRST_ITEM_ROW + SETTINGS_ITEM_COUNT):
        name = str(settings_ws.cell(r, 1).value or '').strip()
        raw = settings_ws.cell(r, 2).value
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            policy[name] = None
        else:
            policy[name] = int(raw)
    warning = settings_ws[SETTINGS_WARNING_CELL].value
    return policy, int(warning) if warning is not None else None


def parse_tracker(tracker_ws, settings_ws, as_at: date) -> tuple[list[TrackerPerson], list[DateException], list[str]]:
    """Read people plus raw issue cells; classify every date problem without fixing it."""
    items = _item_names(settings_ws)
    people: list[TrackerPerson] = []
    date_exceptions: list[DateException] = []
    derived: list[str] = []
    for r in range(TRACKER_FIRST_BODY_ROW, tracker_ws.max_row + 1):
        if tracker_ws.cell(r, TRACKER_NAME_COL).value is None:
            continue
        person = TrackerPerson(
            row=r,
            mine_number=normalize_mine_number(tracker_ws.cell(r, TRACKER_MINE_NO_COL).value),
            name=normalize_name(tracker_ws.cell(r, TRACKER_NAME_COL).value),
            cost_centre=str(tracker_ws.cell(r, TRACKER_COST_CENTRE_COL).value or '').strip(),
        )
        for index, col in enumerate(TRACKER_TRIPLET_COLS):
            cell = tracker_ws.cell(r, col)
            value = cell.value
            person.items[items[index]] = {'issued_raw': value, 'cell': cell.coordinate}
            if value is None:
                pass
            elif (day := _as_date(value)) is not None:
                if day > as_at:
                    date_exceptions.append(DateException('future', cell.coordinate, day.isoformat()))
                if day.year < REVIEW_YEAR_CUTOFF:
                    date_exceptions.append(DateException('pre_2020', cell.coordinate, day.isoformat()))
            else:
                date_exceptions.append(DateException('invalid', cell.coordinate, str(value)))
            for dc in (col + 1, col + 2):
                other = tracker_ws.cell(r, dc)
                if not (isinstance(other.value, str) and other.value.startswith('=')):
                    derived.append(other.coordinate)
        people.append(person)
    return people, date_exceptions, derived


def parse_issue_log(log_ws) -> tuple[list[dict[str, Any]], list[int]]:
    """Complete rows (A-G all present) with cell references; incomplete row numbers quarantined."""
    complete: list[dict[str, Any]] = []
    incomplete: list[int] = []
    for r in range(LOG_FIRST_BODY_ROW, log_ws.max_row + 1):
        values = [log_ws.cell(r, c).value for c in range(1, LOG_COLUMN_COUNT + 1)]
        if all(v is None for v in values):
            continue
        if all(values[c] is not None for c in range(LOG_REQUIRED_COLS)):
            complete.append({'row': r, 'cells': {c + 1: values[c] for c in range(LOG_COLUMN_COUNT)}})
        else:
            incomplete.append(r)
    return complete, incomplete


def read_workbook(path: str, as_at: date) -> WorkbookReading:
    """Parse all four sheets from a file path. Used by rehearsal tooling, not by the API."""
    from openpyxl import load_workbook
    workbook = load_workbook(path, data_only=False)
    try:
        tracker_ws = workbook['PPE Tracker']
        settings_ws = workbook['Settings']
        log_ws = workbook['Issue Log']
        policy, warning_days = parse_policy(settings_ws)
        people, date_exceptions, derived = parse_tracker(tracker_ws, settings_ws, as_at)
        complete, incomplete = parse_issue_log(log_ws)
        return WorkbookReading(
            people=people, policy=policy, warning_days=warning_days,
            log_complete=complete, log_incomplete_rows=incomplete,
            date_exceptions=date_exceptions, derived_nonformula_cells=derived,
        )
    finally:
        workbook.close()


@dataclass
class Reconciliation:
    """Outcome of matching source people against the live register (no writes)."""
    matched: list[dict[str, Any]] = field(default_factory=list)
    create: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


def reconcile_people(people: list[TrackerPerson], register: list[dict[str, Any]]) -> Reconciliation:
    """Match by normalized mine number only. Cross-department numbers, duplicate
    register ids and same-number name differences are conflicts for explicit
    resolution — never automatic merges, moves or overwrites."""
    by_number: dict[str, list[dict[str, Any]]] = {}
    for emp in register:
        key = normalize_mine_number(emp.get('employee_id'))
        if key:
            by_number.setdefault(key, []).append(emp)
    out = Reconciliation()
    for person in people:
        holders = by_number.get(person.mine_number, [])
        if not holders:
            out.create.append({'mine_number': person.mine_number, 'name': person.name,
                               'cost_centre': person.cost_centre, 'row': person.row})
            continue
        if len(holders) > 1:
            out.conflicts.append({'reason': 'duplicate_register_number', 'mine_number': person.mine_number,
                                  'register_ids': [h.get('id') for h in holders], 'row': person.row})
            continue
        emp = holders[0]
        if (emp.get('department') or '') != 'Mining':
            out.conflicts.append({'reason': 'cross_department_match', 'mine_number': person.mine_number,
                                  'register_id': emp.get('id'), 'register_department': emp.get('department'),
                                  'row': person.row})
            continue
        live_name = normalize_name(f"{emp.get('first_name') or ''} {emp.get('last_name') or ''}".strip())
        if live_name and live_name.upper() != person.name.upper():
            out.conflicts.append({'reason': 'name_differs', 'mine_number': person.mine_number,
                                  'register_id': emp.get('id'), 'register_name': live_name,
                                  'source_name': person.name, 'row': person.row})
            continue
        out.matched.append({'mine_number': person.mine_number, 'register_id': emp.get('id'), 'row': person.row})
    return out
