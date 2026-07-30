# app/aggregation.py — shared counting/grouping helpers for stats endpoints.
#
# The same ~5-line pattern (loop records, `counts[k] = counts.get(k, 0) + 1`)
# was hand-rolled independently in 16 routers (maintenance, ppe, notices,
# training, timesheets, requisitions, pto, vfl, near_miss, work_stoppage,
# pachedu, sheq_inspections, compressors, breakdowns, ai_safety, reports).
# count_by/sum_by are higher-order functions — they take the "how to group a
# record" logic as a parameter (a field name or a function) instead of each
# router re-writing the loop around its own field name.

from collections import Counter, defaultdict
from typing import Any, Callable, Iterable, Union

KeyFn = Union[str, Callable[[dict], Any]]


def _resolve_key(key: KeyFn, default: Any) -> Callable[[dict], Any]:
    return key if callable(key) else (lambda r: r.get(key, default))


def count_by(records: Iterable[dict], key: KeyFn, default: Any = 'unknown') -> dict:
    """Count records grouped by a field name (`count_by(rows, 'status')`) or a
    key function (`count_by(rows, lambda r: r['a'] or r['b'])`). Returns a
    plain dict, not Counter, so it JSON-serializes directly in a response."""
    key_fn = _resolve_key(key, default)
    return dict(Counter(key_fn(r) for r in records))


def sum_by(records: Iterable[dict], key: KeyFn, value: KeyFn, default: Any = 'unknown') -> dict:
    """Like count_by, but sums a numeric field per group instead of counting
    rows — e.g. `sum_by(rows, 'department', 'downtime_minutes')`."""
    key_fn = _resolve_key(key, default)
    value_fn = _resolve_key(value, 0)
    totals: dict = defaultdict(float)
    for r in records:
        totals[key_fn(r)] += value_fn(r) or 0
    return dict(totals)
