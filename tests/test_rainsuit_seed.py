# tests/test_rainsuit_seed.py — Batch 5: raincoat requirements seed SQL.
#
# The generated seed inserts one requirement row per allocation entry with a
# re-run guard and per-row conflict skips. Tests pin the safety properties:
# no ppe_records writes appear, no issue dates are stored, sizes keep their
# source values, and output is byte-identical across runs.
from app.rainsuit_seed import render_seed_sql
from app.rainsuit_workbook import RequirementRow


def _rows():
    return [
        RequirementRow(row=5, name='David Muronda', department='Engineering',
                       size_source='L', size_canonical='L', quantity=1,
                       reference='30/10/2026', notes=''),
        RequirementRow(row=12, name='L. Phiri', department='Engineering',
                       size_source='XL', size_canonical='XL', quantity=1,
                       reference='30/10/2026', notes='Size appears XL; verify'),
    ]


def test_seed_inserts_requirements_only():
    sql = render_seed_sql(_rows())
    assert 'ppe_requirements' in sql
    assert 'insert into public.ppe_records' not in sql
    assert 'issue_date' not in sql
    assert "'David Muronda'" in sql and "'L. Phiri'" in sql
    assert 'Size appears XL; verify' in sql
    assert 'ON CONFLICT DO NOTHING' in sql


def test_seed_output_is_deterministic():
    assert render_seed_sql(_rows()) == render_seed_sql(_rows())


def test_seed_applies_reviewed_links():
    sql = render_seed_sql(_rows(), links={
        'David Muronda': {'mine_number': 'PM341', 'section': 'Mechanical'},
        'L. Phiri': {'mine_number': '', 'size_canonical': 'XL', 'section': ''},
    })
    assert "'PM341', 'David Muronda'" in sql
    assert "'Engineering', 'Mechanical'" in sql
    assert "'XL', 'XL'" in sql
    assert "'L. Phiri'" in sql and "'PM341', 'L. Phiri'" not in sql


def test_seed_keeps_source_size_beside_canonical():
    sql = render_seed_sql(_rows())
    assert "'L', 'L'" in sql
