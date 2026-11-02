# backend/app/rainsuit_seed.py - Raincoat requirements seed SQL renderer (Batch 5).
#
# Renders a reviewable, re-runnable SQL file from parsed allocation rows. Each
# row becomes one ppe_requirements entry: source name and size preserved,
# mine number left NULL until the owner confirms the register link, status
# 'required'. No ppe_records writes, no issue dates. IDs are deterministic
# UUID5 values so re-runs compare byte-identical. No timestamps embedded.
from __future__ import annotations

import uuid

from app.rainsuit_workbook import SOURCE_REF, RequirementRow

_SEED_NAMESPACE = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')


def _row_id(name: str) -> str:
    return str(uuid.uuid5(_SEED_NAMESPACE, f'{SOURCE_REF}#rainsuit#{name.strip().upper()}'))


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _nullable(value: str) -> str:
    return 'NULL' if not value else _lit(value)


def render_seed_sql(rows: list[RequirementRow]) -> str:
    """Render the full requirements seed for the parsed allocation rows."""
    people = len(rows)
    total = sum(item.quantity for item in rows)
    out = [
        '-- Raincoat requirements seed (generated; review before running).',
        f'-- Source: Engineering_Raincoat_Allocation_2026_Final.xlsx; people: {people}; rainsuits: {total}.',
        "-- These are size requirements, never issues: no ppe_records rows and",
        "-- no issue dates. Mine numbers link on owner confirmation; NULL until then.",
        '-- Re-runnable: rows use ON CONFLICT DO NOTHING on (source_ref, ppe_type, source_name).',
        '-- Dry run: read the NOTICE output, then decide.',
        'begin;',
        '',
        'do $$',
        'declare requirement_count int;',
        'begin',
        f"  select count(*) into requirement_count from public.ppe_requirements where source_ref = {_lit(SOURCE_REF)};",
        "  raise notice 'DRY RUN raincoat requirements seed: ppe_requirements=%', requirement_count;",
        'end $$;',
        '',
    ]
    for item in rows:
        out.append(
            "insert into public.ppe_requirements"
            " (id, employee_id, source_name, employee_name, department, mine_section,"
            " ppe_type, size_source, size_canonical, quantity, status, source_ref, notes, created_by, row_version) values"
            f" ({_lit(_row_id(item.name))}::uuid, NULL, {_lit(item.name)}, {_lit(item.name)},"
            f" {_nullable(item.department)}, NULL,"
            f" 'rainsuit', {_nullable(item.size_source)}, {_nullable(item.size_canonical or '')},"
            f" {item.quantity}, 'required', {_lit(SOURCE_REF)}, {_nullable(item.notes)},"
            f" 'workbook-import', 1)"
            f" ON CONFLICT DO NOTHING; -- allocation row {item.row}"
        )
    out += ['', 'commit;', '']
    return '\n'.join(out)
