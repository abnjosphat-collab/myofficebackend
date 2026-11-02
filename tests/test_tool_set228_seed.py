# tests/test_tool_set228_seed.py — Batch 3: SET-228 catalogue seed SQL.
#
# The generated seed inserts the set type plus its itemized lines with a
# re-run guard and per-line conflict skips. Tests pin the safety properties:
# the guard, the line count, the piece total, the retained duplicate, and
# byte-identical output across runs (deterministic IDs, no timestamps).
from app.tool_set228_seed import render_seed_sql
from app.tool_set228_workbook import ContentsReading, SetContentLine


def _reading():
    return ContentsReading(lines=[
        SetContentLine(row=4, category='Sockets & ratchets', name='Flexible extension bar',
                       size='1/4 in x 150 mm', qty=1, unit='piece', notes=''),
        SetContentLine(row=5, category='Sockets & ratchets', name='Flexible extension bar',
                       size='1/4 in x 150 mm', qty=1, unit='piece',
                       notes='Second occurrence printed separately in source'),
        SetContentLine(row=6, category='Fixings', name='Screw', size='ST4',
                       qty=10, unit='piece', notes=''),
    ])


def test_seed_declares_guard_type_and_all_lines():
    sql = render_seed_sql(_reading())
    assert 'SET-228' in sql
    assert sql.count('set_type_lines') >= 3
    assert "'Flexible extension bar'" in sql
    assert 'Second occurrence printed separately in source' in sql
    assert 'printed pieces: 12' in sql  # dynamic total follows the fixture
    assert 'not exists' in sql  # re-run guard on the type title
    assert 'ON CONFLICT DO NOTHING' in sql


def test_seed_output_is_deterministic():
    assert render_seed_sql(_reading()) == render_seed_sql(_reading())


def test_seed_escapes_single_quotes():
    reading = ContentsReading(lines=[
        SetContentLine(row=4, category="Bits & holders", name="Tradesman's pick",
                       size='1/4 in', qty=1, unit='piece', notes=""),
    ])
    sql = render_seed_sql(reading)
    assert "Tradesman''s pick" in sql
