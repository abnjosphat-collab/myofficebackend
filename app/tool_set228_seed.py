# backend/app/tool_set228_seed.py - SET-228 catalogue seed SQL renderer (Batch 3).
#
# Renders a reviewable, re-runnable SQL file from a parsed ContentsReading.
# The type insert is guarded on the title (no unique constraint exists, so a
# WHERE NOT EXISTS guard owns idempotency); lines ride the
# (set_type_id, line_no) unique key with ON CONFLICT DO NOTHING. IDs are
# deterministic UUID5 values so re-runs and reviews compare byte-identical.
# No timestamps are embedded.
from __future__ import annotations

import uuid

from app.tool_set228_workbook import SET_CODE, ContentsReading

SET_TITLE = 'Total 228 Piece Hand Tools Set (SET-228)'
_TYPE_NAMESPACE = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')


def _type_id() -> str:
    return str(uuid.uuid5(_TYPE_NAMESPACE, SET_TITLE))


def _line_id(line_no: int) -> str:
    return str(uuid.uuid5(_TYPE_NAMESPACE, f'{SET_TITLE}#{line_no}'))


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_seed_sql(reading: ContentsReading) -> str:
    """Render the full seed file for the parsed set contents."""
    total = reading.total_qty
    out = [
        '-- SET-228 catalogue seed (generated; review before running).',
        f'-- Set type: {SET_TITLE}; lines: {reading.line_count}; printed pieces: {total}.',
        '-- Advertised 228 vs printed 227: reported, never adjusted; the printed',
        '-- duplicate line is retained as printed. Re-runnable: the type insert',
        '-- is guarded on the title and lines use ON CONFLICT DO NOTHING.',
        '-- Dry run: read the NOTICE output, then decide.',
        'begin;',
        '',
        'do $$',
        'declare type_count int; line_count int;',
        'begin',
        f"  select count(*) into type_count from public.tools_workspace_set_types where title = {_lit(SET_TITLE)};",
        "  select count(*) into line_count from public.tools_workspace_set_type_lines",
        f"    where set_type_id in (select id from public.tools_workspace_set_types where title = {_lit(SET_TITLE)});",
        "  raise notice 'DRY RUN SET-228 seed: set_types=% set_type_lines=%', type_count, line_count;",
        'end $$;',
        '',
        f"insert into public.tools_workspace_set_types",
        '  (id, title, make_model, category, competency_required, notes, active, created_by, row_version)',
        'select',
        f"  {_lit(_type_id())}::uuid, {_lit(SET_TITLE)}, NULL, 'Hand tools', false,",
        f"  {_lit('Seeded from Total_228_Piece_Hand_Tools_Set.xlsx (packaging photograph, 2 Nov 2026). Physical count recommended before first issue.')},",
        '  true, \'workbook-import\', 1',
        f"where not exists (select 1 from public.tools_workspace_set_types where title = {_lit(SET_TITLE)});",
        '',
    ]
    for position, line in enumerate(reading.lines, start=1):
        size = 'NULL' if not line.size else _lit(line.size)
        notes = 'NULL' if not line.notes else _lit(line.notes)
        out.append(
            f"insert into public.tools_workspace_set_type_lines"
            f" (id, set_type_id, line_no, name, size, qty, notes) values"
            f" ({_lit(_line_id(position))}::uuid,"
            f" (select id from public.tools_workspace_set_types where title = {_lit(SET_TITLE)}),"
            f" {position}, {_lit(line.name)}, {size}, {line.qty}, {notes})"
            f" ON CONFLICT DO NOTHING; -- source row {line.row}: {line.category}"
        )
    out += ['', 'commit;', '']
    return '\n'.join(out)
