# tests/test_rainsuit_workbook.py — Batch 2: raincoat allocation reader and size reconciliation.
#
# Read-only parsing of the Engineering raincoat workbook into staged size
# requirements plus an explicit exception queue. Nothing here writes to the
# database, creates employees, fabricates issues, or guesses sizes: unknown
# sizes, flagged handwriting, and unmatched names stay visible for review.
from openpyxl import Workbook

from app.rainsuit_workbook import (
    CANONICAL_SIZES,
    SIZE_SUMMARY_ORDER,
    canonical_size,
    parse_allocation,
    parse_size_summary,
    reconcile_people,
    summarize,
)


def _workbook(rows):
    wb = Workbook()
    alloc = wb.active
    alloc.title = 'Raincoat Allocation'
    for header, col in zip(['No.', 'Department', 'Employee Name', 'Size',
                            'Quantity', 'Date / Reference', 'Verification Notes'], range(1, 8)):
        alloc.cell(4, col, header)
    for n, (dept, name, size, qty, ref, notes) in enumerate(rows, start=5):
        alloc.cell(n, 1, n - 4)
        alloc.cell(n, 2, dept)
        alloc.cell(n, 3, name)
        alloc.cell(n, 4, size)
        alloc.cell(n, 5, qty)
        alloc.cell(n, 6, ref)
        alloc.cell(n, 7, notes)
    summary = wb.create_sheet('Size Summary')
    for i, size in enumerate(SIZE_SUMMARY_ORDER):
        summary.cell(4 + i, 1, size)
    summary.cell(11, 1, 'TOTAL')
    return alloc, summary


def _emp(id, first, last, number=None, department='Engineering'):
    return {'id': id, 'employee_id': number, 'department': department,
            'first_name': first, 'last_name': last}


def test_allocation_parses_requirements_not_issues():
    alloc, _ = _workbook([
        ('Engineering', 'David Muronda', 'L', 1, '30/10/2026', None),
        ('Engineering', 'L. Phiri', 'XL', 1, '30/10/2026', 'Size appears XL; verify'),
    ])
    reading = parse_allocation(alloc)
    assert len(reading.rows) == 2
    first = reading.rows[0]
    assert first.name == 'David Muronda' and first.size_source == 'L'
    assert first.size_canonical == 'L' and first.quantity == 1
    assert first.issue_date is None and first.is_issue is False
    assert reading.rows[1].notes == 'Size appears XL; verify'


def test_canonical_sizes_accept_aliases_and_never_guess():
    assert canonical_size('xxl') == 'XXL'
    assert canonical_size('Raincoat - XL') == 'XL'
    assert canonical_size('') is None
    assert canonical_size('Huge') is None
    assert set(CANONICAL_SIZES) == {'S', 'M', 'L', 'XL', 'XXL', 'XXXL'}


def test_summary_reconciles_and_shows_zero_m():
    alloc, _ = _workbook([
        ('Engineering', 'A', 'L', 1, '30/10/2026', None),
        ('Engineering', 'B', 'XXL', 1, '30/10/2026', None),
        ('Engineering', 'C', None, 1, '30/10/2026', None),
    ])
    summary = summarize(parse_allocation(alloc))
    assert summary.counts == {'L': 1, 'XXL': 1}
    assert summary.unknown == 1
    assert summary.total_quantity == 3
    assert summary.people == 3
    assert summary.by_size('M') == 0


def test_total_footer_row_is_not_a_person():
    alloc, _ = _workbook([('Engineering', 'A', 'L', 1, '30/10/2026', None)])
    alloc.cell(30, 3, 'TOTAL RAINCOATS')
    alloc.cell(30, 5, 24)
    reading = parse_allocation(alloc)
    assert [row.name for row in reading.rows] == ['A']
    assert summarize(reading).total_quantity == 1


def test_size_summary_sheet_lists_every_size_once():
    _, summary_ws = _workbook([])
    assert parse_size_summary(summary_ws) == SIZE_SUMMARY_ORDER


def test_reconciliation_links_tokens_and_quarantines_rest():
    register = [
        _emp(1, 'David', 'Muronda', 'C1001'),
        _emp(2, 'Anna', 'Diva', 'C1002'),
        _emp(3, 'Rudo', 'Phiri', 'C1003'),
        _emp(4, 'Lydia', 'Phiri', 'C1004'),
    ]
    result = reconcile_people(
        [{'row': 5, 'name': 'David Muronda'}, {'row': 9, 'name': 'M. Diva'},
         {'row': 12, 'name': 'L. Phiri'}, {'row': 28, 'name': 'T. Kapenzi'}],
        register)
    assert [(m['source_name'], m['register_id']) for m in result.matched] == [
        ('David Muronda', 1), ('M. Diva', 2)]
    assert [(c['source_name'], c['reason']) for c in result.conflicts] == [
        ('L. Phiri', 'ambiguous_surname')]
    assert [(u['source_name'], u['reason']) for u in result.unmatched] == [
        ('T. Kapenzi', 'no_token_match')]
