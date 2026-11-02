# tests/test_tool_set228_workbook.py — Batch 1: SET-228 workbook reader and recipient reconciliation.
#
# Read-only parsing of the 228-piece hand-tools workbook into staged set-type
# lines plus an explicit exception queue, and matching of the 18 owner-supplied
# recipient surnames against a register. Nothing here writes to the database,
# creates employees, or guesses corrections: the 228-vs-227 gap, the printed
# duplicate line, and every ambiguous/unmatched name stay visible for review.
from openpyxl import Workbook

from app.tool_set228_workbook import (
    INTENDED_RECIPIENTS,
    SET_CODE,
    parse_contents,
    parse_issue_log,
    parse_overview,
    reconcile_recipients,
)


def _overview(ws):
    ws['A1'] = 'TOOLS MODULE | COMPLETE HAND-TOOLS KIT'
    for r, (label, value) in enumerate([
        ('Set code', 'SET-228'),
        ('Set description', 'Total 228 Piece Hand Tools Set'),
        ('Unit of issue', '1 complete set'),
        ('Advertised pieces', 228),
        ('Pieces itemized on printed photo', 227),
        ('Unreconciled advertised pieces', 1),
        ('Distinct item variants', 135),
        ('Verification', 'Physical count recommended before first issue'),
        ('Source', 'Uploaded packaging photograph, 2 Nov 2026'),
    ], start=3):
        ws.cell(r, 1, label)
        ws.cell(r, 2, value)
    ws['A13'] = 'ISSUING RULE: Record quantity 1 in the Set Issue Log. The full itemized contents follow on Set Contents.'
    ws['A15'] = 'NOTE: Printed quantities sum to 227, one below the advertised 228.'


def _contents(ws, rows):
    for header, col in zip(['Set Code', 'Category', 'Tool / Item', 'Specification / Size',
                             'Qty per Set', 'Unit', 'Source Notes'], range(1, 8)):
        ws.cell(3, col, header)
    for n, (cat, name, size, qty, notes) in enumerate(rows, start=4):
        ws.cell(n, 1, 'SET-228')
        ws.cell(n, 2, cat)
        ws.cell(n, 3, name)
        ws.cell(n, 4, size)
        ws.cell(n, 5, qty)
        ws.cell(n, 6, 'piece')
        ws.cell(n, 7, notes)


def _workbook(contents_rows):
    wb = Workbook()
    overview = wb.active
    overview.title = 'Set Overview'
    contents = wb.create_sheet('Set Contents')
    log = wb.create_sheet('Set Issue Log')
    _overview(overview)
    _contents(contents, contents_rows)
    for header, col in zip(['Issue Date', 'Issue Ref', 'Set Code', 'Issued To / Employee',
                            'Department / Section', 'Sets Issued',
                            'Returned Date', 'Remarks / Condition'], range(1, 9)):
        log.cell(4, col, header)
    return overview, contents, log


def _emp(id, first, last, number=None, department='Mining'):
    return {'id': id, 'employee_id': number, 'department': department,
            'first_name': first, 'last_name': last}


def test_overview_parses_stated_totals_without_adjusting():
    overview, _, _ = _workbook([])
    info = parse_overview(overview)
    assert info['set_code'] == SET_CODE == 'SET-228'
    assert info['advertised_pieces'] == 228
    assert info['printed_pieces'] == 227
    assert info['unreconciled'] == 1
    assert info['distinct_variants'] == 135
    assert 'Physical count' in info['verification']


def test_contents_sum_and_duplicate_stay_visible():
    _, contents, _ = _workbook([
        ('Sockets & ratchets', 'Flexible extension bar', '1/4 in x 150 mm', 1, None),
        ('Sockets & ratchets', 'Flexible extension bar', '1/4 in x 150 mm', 1,
         'Second occurrence printed separately in source'),
        ('Fixings', 'Screw', 'ST4', 10, None),
    ])
    reading = parse_contents(contents)
    assert reading.line_count == 3
    assert reading.total_qty == 12
    assert reading.duplicates == [{'name': 'Flexible extension bar',
                                   'size': '1/4 in x 150 mm', 'rows': [4, 5]}]


def test_issue_log_blank_template_counts_no_history():
    _, _, log = _workbook([])
    for r in range(5, 15):
        log.cell(r, 3, 'SET-228')
    result = parse_issue_log(log)
    assert result.complete == []
    assert result.incomplete == []
    assert result.blank_template_rows == 10


def test_issue_log_partial_row_is_quarantined():
    _, _, log = _workbook([])
    log.cell(5, 1, '2026-11-05')
    log.cell(5, 3, 'SET-228')
    result = parse_issue_log(log)
    assert result.complete == []
    assert result.incomplete == [5]


def test_recipients_are_18_with_bhunu_once():
    assert len(INTENDED_RECIPIENTS) == 18
    assert sorted(INTENDED_RECIPIENTS).count('Bhunu') == 1


def test_reconciliation_matches_splits_and_never_fuzzy_merges():
    register = [
        _emp(1, 'David', 'Muronda', 'C1001'),
        _emp(2, 'Alex', 'Muronda', 'C1002'),
        _emp(3, 'Tariro', 'Bhunu', 'C1003'),
    ]
    result = reconcile_recipients(['Muronda', 'Bhunu', 'Nobodyhere'], register)
    assert [m['source_name'] for m in result.matched] == ['Bhunu']
    assert result.matched[0]['register_id'] == 3
    assert [(c['source_name'], c['reason']) for c in result.conflicts] == [('Muronda', 'ambiguous_surname')]
    assert [u['source_name'] for u in result.unmatched] == ['Nobodyhere']
    # The ambiguous surname must not silently pick either holder.
    assert all(m['source_name'] != 'Muronda' for m in result.matched)
