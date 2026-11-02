# tests/test_mining_ppe_workbook.py - SOLUTION batch 1: Mining workbook reader and reconciliation.
#
# Pure parser tests over small synthetic workbooks built in-test with openpyxl.
# All names, numbers and dates are invented; nothing here touches the real
# workbook, the database or the network.
from datetime import date

import pytest
from openpyxl import Workbook

from app.mining_ppe_workbook import (
    normalize_mine_number,
    parse_issue_log,
    parse_policy,
    parse_tracker,
    reconcile_people,
    TrackerPerson,
)

ITEMS = ['Overalls', 'Gumboots', 'Safety Chain', 'Hard Hat', 'Sinking Suit', 'Harness', 'Safety Shoes', 'Jackets']
INTERVALS = [6, 3, 24, 24, 3, None, 6, None]


def _settings(ws):
    for i, (item, months) in enumerate(zip(ITEMS, INTERVALS)):
        ws.cell(5 + i, 1, item)
        if months is not None:
            ws.cell(5 + i, 2, months)
    ws['B15'] = 30


def _tracker(ws, rows):
    # rows: (mine_number, name, cost_centre, {item: issued_value})
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


def _workbook(tracker_rows, log_rows=()):
    wb = Workbook()
    tracker = wb.active
    tracker.title = 'PPE Tracker'
    settings = wb.create_sheet('Settings')
    log = wb.create_sheet('Issue Log')
    summary = wb.create_sheet('Summary')
    _settings(settings)
    _tracker(tracker, tracker_rows)
    for n, values in enumerate(log_rows, start=5):
        for c, value in enumerate(values, start=1):
            log.cell(n, c, value)
    return wb, tracker, settings, log


def _emp(id, number, department='Mining', first='Test', last='Person'):
    return {'id': id, 'employee_id': number, 'department': department,
            'first_name': first, 'last_name': last}


def test_tracker_people_items_and_policy():
    wb, tracker, settings, _log = _workbook([
        ('C0001', 'Test One', 'MINE PRODUCTION', {'Overalls': date(2026, 1, 10)}),
        ('C0002', 'Test Two', 'GEOLOGY', {}),
    ])
    people, exceptions, derived = parse_tracker(tracker, settings, date(2026, 11, 1))
    assert [(p.mine_number, p.name, p.cost_centre) for p in people] == [
        ('C0001', 'Test One', 'MINE PRODUCTION'), ('C0002', 'Test Two', 'GEOLOGY')]
    assert people[0].items['Overalls']['issued_raw'] == date(2026, 1, 10)
    assert people[1].items['Gumboots']['issued_raw'] is None
    assert exceptions == []
    assert derived == []
    policy, warning = parse_policy(settings)
    assert policy == dict(zip(ITEMS, INTERVALS))
    assert warning == 30


def test_unknown_policy_stays_unknown_not_zero():
    wb, _tracker_ws, settings, _log = _workbook([])
    policy, _warning = parse_policy(settings)
    assert policy['Harness'] is None
    assert policy['Jackets'] is None


def test_date_problems_are_classified_never_fixed():
    wb, tracker, settings, _log = _workbook([(
        'C0001', 'Test One', 'MINE PRODUCTION',
        {'Overalls': '3/31/2003/26', 'Gumboots': date(2026, 12, 8), 'Safety Chain': date(2019, 5, 1)},
    )])
    _people, exceptions, _derived = parse_tracker(tracker, settings, date(2026, 11, 1))
    by_kind = {}
    for e in exceptions:
        by_kind.setdefault(e.kind, []).append(e.cell[0])
    assert by_kind['invalid'] == ['D']
    assert by_kind['future'] == ['G']
    assert by_kind['pre_2020'] == ['J']


def test_hardcoded_derived_cells_are_flagged():
    wb, tracker, settings, _log = _workbook([
        ('C0001', 'Test One', 'MINE PRODUCTION', {'Overalls': date(2026, 1, 10)}),
    ])
    tracker['E6'] = date(2026, 7, 10)  # next-due written over the formula
    _people, _exceptions, derived = parse_tracker(tracker, settings, date(2026, 11, 1))
    assert 'E6' in derived


def test_issue_log_complete_rows_link_and_fragments_quarantine():
    wb, _t, _s, log = _workbook([], [
        ['2026-09-04', 'C0001', 'Test One', 'MINE PRODUCTION', 'Overalls', 2, 'Issuer A', None],
        ['1057'],
    ])
    complete, incomplete = parse_issue_log(log)
    assert [r['row'] for r in complete] == [5]
    assert complete[0]['cells'][6] == 2
    assert incomplete == [6]


def test_mine_number_is_verbatim_uppercase_without_inferred_letters():
    assert normalize_mine_number(' c1057 ') == 'C1057'
    assert normalize_mine_number('1057') == '1057'
    assert normalize_mine_number(None) == ''


def test_reconciliation_matches_creates_and_conflicts():
    people = [
        TrackerPerson(6, 'C0001', 'Test One', 'MINE PRODUCTION', {}),
        TrackerPerson(7, 'C0002', 'Test Two', 'MINE PRODUCTION', {}),
        TrackerPerson(8, 'C0003', 'Test Three', 'MINE PRODUCTION', {}),
        TrackerPerson(9, 'C0004', 'Source Name', 'MINE PRODUCTION', {}),
        TrackerPerson(10, 'ENG9', 'Test Five', 'MINE PRODUCTION', {}),
    ]
    register = [
        _emp(1, 'C0001', first='Test', last='One'),
        _emp(2, 'C0004', first='Live', last='Other'),
        _emp(3, 'ENG9', first='Wrong', last='Dept'),
        _emp(4, 'ENG9', first='Wrong', last='Dept2'),
        _emp(5, 'C0003', department='Engineering', first='Test', last='Three'),
    ]
    result = reconcile_people(people, register)
    assert [m['mine_number'] for m in result.matched] == ['C0001']
    assert [c['mine_number'] for c in result.create] == ['C0002']
    reasons = {c['mine_number']: c['reason'] for c in result.conflicts}
    assert reasons == {'C0004': 'name_differs', 'ENG9': 'duplicate_register_number',
                       'C0003': 'cross_department_match'}


def test_blank_source_numbers_never_match():
    people = [TrackerPerson(6, '', 'No Number', 'MINE PRODUCTION', {})]
    result = reconcile_people(people, [_emp(1, 'C0001')])
    assert [c['mine_number'] for c in result.create] == ['']
    assert result.matched == [] and result.conflicts == []
