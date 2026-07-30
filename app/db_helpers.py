# app/db_helpers.py — shared Supabase query helpers. Each of these was hand-
# rolled independently (byte-identical or near-identical) across many routers;
# consolidated here so a fix or behavior change only has to be made once.
#
# get_or_404 / apply_date_range / or_ilike / distinct_suggestions are higher-
# order in spirit even though none literally takes a function: each replaces
# a hand-written loop/query-building block with a call parameterized by the
# table/column/value that varied per call site, the same idea as count_by.

from typing import Any, Callable, List, Optional

from fastapi import HTTPException

# db (the Supabase client) is a required parameter here, not a module-level
# import — every router's tests monkeypatch that router's own `supabase` name
# (looked up at call time in the router's frame, e.g. `monkeypatch.setattr
# (requisitions_mod, "supabase", fake)`), not app.supabase_client's original
# object. If this helper imported its own `supabase`, the router's mock would
# never reach it. Callers pass their own already-imported `supabase` through,
# so whichever object is currently bound to that name — real or faked — is
# what actually runs.


def get_or_404(db, table: str, id_value: Any, *, id_col: str = "id", detail: str = "Not found") -> dict:
    """Fetch one row by id, or raise a 404. Replaces the
    `select().eq("id", x).execute(); if not r.data: raise HTTPException(404, ...)`
    shape that was repeated ~30 times across 17 routers."""
    r = db.table(table).select("*").eq(id_col, id_value).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail=detail)
    return r.data[0]


def apply_date_range(query, column: str, date_from: Optional[str] = None, date_to: Optional[str] = None):
    """Apply an optional gte/lte date-range filter to a Supabase query builder.
    Returns the (possibly unmodified) query so it chains like the rest of the
    builder API."""
    if date_from:
        query = query.gte(column, date_from)
    if date_to:
        query = query.lte(column, date_to)
    return query


def or_ilike(columns: List[str], term: str) -> str:
    """Build the `.or_()` filter string for a case-insensitive multi-column
    search — the same expression crud_router.py already used internally,
    pulled out so the non-CRUD routers (which need custom logic alongside
    the search) can share it instead of retyping the string-join."""
    return ",".join(f"{c}.ilike.%{term}%" for c in columns)


async def distinct_suggestions(db, table: str, column: str, search: Optional[str], log_label: str) -> list:
    """Sorted distinct non-empty values of one column, optionally filtered by
    a search term — the shape behind every page's "type to see past values"
    suggestion endpoint (department, observer, inspector, ...). Swallows
    errors to an empty list, matching every call site's original fallback
    behavior (a broken suggestions endpoint shouldn't break the form)."""
    try:
        query = db.table(table).select(column).neq(column, "").not_.is_(column, "null").order(column)
        if search:
            query = query.ilike(column, f"%{search}%")
        response = query.execute()
        if hasattr(response, "data"):
            return sorted({item[column] for item in response.data if item.get(column)})
        return []
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error fetching {log_label} suggestions: {e}")
        return []


def status_choice_validator(allowed: List[str], message: str) -> Callable:
    """Build a Pydantic v1 `@validator` function body for a fixed-choice
    status/enum-like string field. Callers still write
    `_validate_x = validator('field')(status_choice_validator([...], "..."))`
    so the field name stays explicit at each model, only the repeated
    allowed-list-check body is shared."""
    def _validate(cls, v):
        if v not in allowed:
            raise ValueError(message)
        return v
    return _validate
