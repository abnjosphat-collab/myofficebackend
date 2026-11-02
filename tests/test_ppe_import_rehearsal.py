# tests/test_ppe_import_rehearsal.py — SOLUTION batch 5: fixture end-to-end import rehearsal.
#
# A dry run of the whole preview path on invented data only: parse a synthetic workbook, reconcile against a
# synthetic register, build the exact event payloads the guarded import would post, and prove the rehearsal
# accounting balances (accepted persons x valid dated known-policy items == payloads; everything else stays in
# the review queue) while the Engineering baseline is untouched by the Mining policy seed.
from datetime import date

from openpyxl import Workbook

import app.routers.ppe as ppe_mod
from app.mining_ppe_workbook import (
    TrackerPerson,
    parse_policy,
    parse_tracker,
    reconcile_people,
)
from app.routers.ppe import PPEEventIn, _effective_matrix

ITEMS = ['Overalls', 'Gumboots', 'Safety Chain', 'Hard Hat', 'Sinking Suit', 'Harness', 'Safety Shoes', 'Jackets']
INTERVALS = [6, 3, 24, 24, 3, None, 6, None]
# Preview-stage source-name to server-code mapping (SOLUTION item table).
CODES = {'Overalls': 'overall', 'Gumboots': 'gumboots', 'Safety Chain': 'safety_chain', 'Hard Hat': 'helmet',
         'Sinking Suit': 'sinking_suit', 'Harness': 'harness', 'Safety Shoes': 'safety_shoes', 'Jackets': 'jacket'}


def _settings(ws):
    for i, (item, months) in enumerate(zip(ITEMS, INTERVALS)):
        ws.cell(5 + i, 1, item)
        if months is not None:
            ws.cell(5 + i, 2, months)
    ws['B15'] = 30


def _tracker(ws, rows):
    for n, (number, name, section, issued) in enumerate(rows, start=6):
        ws.cell(n, 1, number)
        ws.cell(n, 2, name)
        ws.cell(n, 3, section)
        for i, item in enumerate(ITEMS):
            col = (4, 7, 10, 13, 16, 19, 22, 25)[i]
            if item in issued:
                ws.cell(n, col, issued[item])
            ws.cell(n, col + 1, '=EDATE(D6,6)')
            ws.cell(n, col + 2, '=IF(E6<TODAY(),"Overdue","OK")')


def _workbook(tracker_rows):
    wb = Workbook()
    tracker = wb.active
    tracker.title = 'PPE Tracker'
    settings = wb.create_sheet('Settings')
    wb.create_sheet('Issue Log')
    wb.create_sheet('Summary')
    _settings(settings)
    _tracker(tracker, tracker_rows)
    return tracker, settings


def _emp(id, number, department='Mining', first='Test', last='Person'):
    return {'id': id, 'employee_id': number, 'department': department,
            'first_name': first, 'last_name': last}


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self._filters = {}

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        self._filters[field] = value
        return self

    def execute(self):
        return _Result([r for r in self._rows
                        if all(r.get(k) == v for k, v in self._filters.items())])


class _FakeSupabase:
    def __init__(self, matrix):
        self._matrix = matrix

    def table(self, name):
        assert name == 'ppe_matrix'
        return _FakeQuery(self._matrix)


def _rehearsal_state():
    tracker, settings = _workbook([
        ('C0001', 'Test One', 'MINE PRODUCTION', {'Overalls': date(2026, 1, 10),
                                                 'Gumboots': date(2026, 12, 8),
                                                 'Safety Chain': date(2026, 2, 2)}),
        ('C0002', 'Test Two', 'GEOLOGY', {}),
        ('C0003', 'Source Name', 'MINE PRODUCTION', {'Safety Shoes': date(2026, 3, 3)}),
    ])
    register = [_emp(1, 'C0001', first='Test', last='One'),
                _emp(2, 'C0003', first='Live', last='Other')]
    return tracker, settings, register


def _preview_payloads():
    tracker, settings, register = _rehearsal_state()
    people, exceptions, _derived = parse_tracker(tracker, settings, date(2026, 11, 1))
    policy, _warning = parse_policy(settings)
    result = reconcile_people(people, register)
    bad_cells = {e.cell for e in exceptions}
    accepted = {m['mine_number']: m for m in result.matched}
    accepted.update({c['mine_number']: c for c in result.create})
    payloads, queued = [], []
    for person in people:
        if person.mine_number not in accepted:
            queued.append((person.mine_number, 'conflict'))
            continue
        for item, months in policy.items():
            cell = person.items[item]
            if cell.get('issued_raw') is None:
                continue
            if months is None:
                queued.append((person.mine_number, f'{item}:unknown-policy'))
                continue
            if cell.get('cell') in bad_cells:
                queued.append((person.mine_number, f'{item}:date-exception'))
                continue
            issued = cell['issued_raw']
            payloads.append(PPEEventIn(
                kind='issue', employee_id=person.mine_number, ppe_type=CODES[item],
                quantity=1, issue_date=issued,
                idempotency_key=f"import:{person.mine_number}:{CODES[item]}:{issued.isoformat()}"))
    return result, exceptions, payloads, queued


def test_rehearsal_preview_balances():
    result, exceptions, payloads, queued = _preview_payloads()
    assert [m['mine_number'] for m in result.matched] == ['C0001']
    assert [c['mine_number'] for c in result.create] == ['C0002']
    assert [c['mine_number'] for c in result.conflicts] == ['C0003']
    assert len(exceptions) == 1 and exceptions[0].kind == 'future'
    # Accepted persons x valid dated known-policy items: C0001 Overalls + Safety Chain only.
    # Gumboots is a quarantined future date; undated items stay missing (never invented); C0003 is a conflict.
    assert [(p.employee_id, p.ppe_type) for p in payloads] == [('C0001', 'overall'), ('C0001', 'safety_chain')]
    assert sorted(queued) == [('C0001', 'Gumboots:date-exception'), ('C0003', 'conflict')]
    for p in payloads:
        assert p.quantity >= 1 and p.idempotency_key.startswith('import:C0001:')


def test_rehearsal_rerun_builds_identical_keys():
    _, _, first, _ = _preview_payloads()
    _, _, second, _ = _preview_payloads()
    assert [p.idempotency_key for p in first] == [p.idempotency_key for p in second]


def test_rehearsal_mining_seed_leaves_engineering_baseline_untouched(monkeypatch):
    matrix = [
        {'ppe_type': 'gumboots', 'interval_months': 6, 'department': ''},
        {'ppe_type': 'gumboots', 'interval_months': 3, 'department': 'Mining'},
        {'ppe_type': 'jacket', 'interval_months': 24, 'department': 'Mining'},
        {'ppe_type': 'harness', 'interval_months': 24, 'department': 'Mining'},
    ]
    monkeypatch.setattr(ppe_mod, 'supabase', _FakeSupabase(matrix))
    engineering = _effective_matrix('Engineering')
    company = _effective_matrix(None)
    mining = _effective_matrix('Mining')
    assert engineering == company
    assert 'jacket' not in engineering and engineering['gumboots'] == 6
    assert mining['jacket'] == 24 and mining['gumboots'] == 3 and mining['harness'] == 24


def test_rehearsal_conflict_never_becomes_a_payload():
    _tracker, _settings, register = _rehearsal_state()
    people = [TrackerPerson(8, 'C0003', 'Source Name', 'MINE PRODUCTION', {}),
              TrackerPerson(9, '', 'No Number', 'MINE PRODUCTION', {})]
    result = reconcile_people(people, register)
    assert result.matched == []
    assert [c['mine_number'] for c in result.create] == ['']
    assert [(c['mine_number'], c['reason']) for c in result.conflicts] == [('C0003', 'name_differs')]
