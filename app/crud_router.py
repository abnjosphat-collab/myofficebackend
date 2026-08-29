"""Generic CRUD router — one correct implementation of the list/create/update/delete
shape that ~30 routers were hand-copying (see audit BE-1).

Before this, every simple registry router (drivers, contractors, competency,
failure_modes, condition_monitoring, production, …) repeated the same four handlers
with slightly different error handling, dump idioms, 404 semantics and route styles.
`CrudRouter` captures the shape once; a router becomes a few lines of config, and
anything non-standard is added by subclassing and appending to `self.router`.

Deliberately Pydantic-**v1**-compatible: uses `model.dict(...)`, not `.model_dump(...)`.
The project pins Pydantic v2 (where `.dict()` is a supported deprecated alias), but we
keep the v1 idiom on purpose — see the project's Render constraint. No `@field_validator`,
no v2-only APIs here.

Behaviour is a faithful union of the hand-written routers it replaces:

- **GET ""** and **GET "/"** — `select *`, optional `search` (ILIKE across `search_columns`),
  optional equality `filters` ({query_param: column}), `order(order_by, desc=order_desc)`,
  optional `default_limit` (also reads `?limit=`). Returns the row list. 500 on error.
  Requires a signed-in user (this business data isn't public).
- **POST ""** / **POST "/"** — insert `body.dict(exclude_none=True)`; 500 if the insert
  returns nothing; returns the created row. Requires a signed-in user.
- **PATCH "/{item_id}"** — update `body.dict(exclude_unset=True)` (explicit nulls clear a
  field, unset fields are left alone — the null-filter fix, backend edec24a); 404 if the
  row doesn't exist; returns the updated row. Requires a signed-in user.
- **DELETE "/{item_id}"** — delete by id; returns `{"ok": True}`. Requires manager+.

`id_type` (optional, default `int`): pass `str` for a table keyed by a UUID/client-
generated id instead of a SERIAL integer (e.g. `notices.py`). Affects PATCH/DELETE's
`/{item_id}` only — the id is always read off the URL as a string and cast per this
setting, so an invalid int id still gets a clean 422, same as a literal `item_id: int`
annotation would have given.

`before_create`/`before_update` (optional): a `dict -> dict` transform applied to the
already-built payload right before it's written — for a table with a genuinely custom
but small write-time need (trimming/filtering a field, stamping a timestamp column with
no DB-level default/trigger) that doesn't justify hand-writing the whole endpoint. See
`drivers.py` for a real example. Don't reach for this to route around a *read*-time
difference (computed fields, joins) — that's still a sign the table needs its own
hand-written endpoint alongside the base, same as `contractors.py`.

Subclass to extend:

    class ContractorsRouter(CrudRouter):
        def __init__(self):
            super().__init__("contractors", ContractorCreate, ContractorUpdate,
                             tags=["Contractors"], order_by="company_name",
                             filters={"status": "status", "trade": "trade"},
                             not_found="Contractor not found")
            # add the non-CRUD endpoints this table also needs:
            self.router.add_api_route("/{c_id}", self.get_one, methods=["GET"])
"""
from datetime import date, datetime
from typing import Callable, Optional, Type

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel

from app.supabase_client import supabase, rows as db_rows, one_row
from app.auth import get_current_user, require_role
from app.db_helpers import or_ilike
import logging

logger = logging.getLogger(__name__)


BeforeSaveHook = Callable[[dict], dict]


def serialize_row(row: dict) -> dict:
    """Convert any date/datetime values to ISO strings. A no-op on the JSON Supabase
    already returns (values are str/num/bool/None/list/dict), but keeps the contract
    correct if a handler ever passes Python date objects through."""
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            row[k] = v.isoformat()
    return row


class CrudRouter:
    def __init__(
        self,
        table: str,
        create_model: Type[BaseModel],
        update_model: Type[BaseModel],
        *,
        tags: Optional[list] = None,
        order_by: str = "id",
        order_desc: bool = False,
        filters: Optional[dict] = None,     # {query_param_name: column_name} applied as .eq
        search_columns: Optional[list] = None,  # ILIKE OR across these when ?search= is given
        default_limit: Optional[int] = None,     # None → unbounded; int → .limit(default) and honour ?limit=
        not_found: str = "Not found",
        reject_empty_update: bool = False,   # 400 on a PATCH that resolves to no fields (job_cards)
        # Transform a payload right before it's written — e.g. trimming/filtering
        # fields, or stamping a timestamp column the table has no DB-level default/
        # trigger for. Applied to the already-built dict (data.dict(...)), not the
        # Pydantic model, and runs after reject_empty_update's check so a hook that
        # unconditionally stamps a field (e.g. updated_at) can't defeat it.
        before_create: Optional[BeforeSaveHook] = None,
        before_update: Optional[BeforeSaveHook] = None,
        # int (default, a SERIAL primary key) or str (a UUID/client-generated id, e.g.
        # notices.py) — several tables in this codebase use string ids, not just one.
        id_type: Type = int,
    ):
        self.table = table
        self.create_model = create_model
        self.update_model = update_model
        self.order_by = order_by
        self.order_desc = order_desc
        self.filters = filters or {}
        self.search_columns = search_columns or []
        self.default_limit = default_limit
        self.not_found = not_found
        self.reject_empty_update = reject_empty_update
        self.before_create = before_create
        self.before_update = before_update
        self.id_type = id_type
        self.router = APIRouter(tags=tags or [])
        self._register()

    def _register(self):
        # Locals captured by the closures below. The body annotations `data: create_model`
        # / `data: update_model` are evaluated at def-time to the concrete model classes,
        # which is what FastAPI reads to parse & validate the request body.
        table = self.table
        create_model = self.create_model
        update_model = self.update_model
        order_by = self.order_by
        order_desc = self.order_desc
        filters = self.filters
        search_columns = self.search_columns
        default_limit = self.default_limit
        not_found = self.not_found
        reject_empty_update = self.reject_empty_update
        before_create = self.before_create
        before_update = self.before_update
        id_type = self.id_type

        def cast_id(raw: str):
            # The path param is always read as str (FastAPI's natural type for a URL
            # segment) so both int- and str-keyed tables share one function signature;
            # int tables get the same "not a valid id" 422 FastAPI would have given
            # for a literal `item_id: int` annotation, just raised here instead.
            if id_type is int:
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=422, detail=f"Invalid id: {raw!r}")
            return raw

        async def list_items(request: Request):
            try:
                q = supabase.table(table).select("*")
                params = request.query_params
                search = params.get("search")
                if search and search_columns:
                    q = q.or_(or_ilike(search_columns, search))
                for param, column in filters.items():
                    value = params.get(param)
                    if value is not None and value != "":
                        q = q.eq(column, value)
                q = q.order(order_by, desc=order_desc)
                if default_limit is not None:
                    try:
                        limit = int(params.get("limit", default_limit))
                    except (TypeError, ValueError):
                        limit = default_limit
                    result = db_rows(q.limit(limit).offset(int(params.get("offset") or 0)).execute())
                else:
                    # No limit configured — Supabase PostgREST caps a single unbounded
                    # .execute() at ~1000 rows by default, which silently truncated
                    # overtime.py's list endpoint once its table grew past that (fixed
                    # separately). Every CrudRouter consumer that doesn't set
                    # default_limit shares this same base query, so the fix belongs
                    # here once rather than in each router — loop with .range() until
                    # a page comes back short.
                    PAGE = 1000
                    result = []
                    start = 0
                    while True:
                        batch = db_rows(q.range(start, start + PAGE - 1).execute())
                        result.extend(batch)
                        if len(batch) < PAGE:
                            break
                        start += PAGE
                return [serialize_row(r) for r in result]
            except Exception as e:
                logger.error(f"[{table}] list failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        async def create_item(data: create_model):
            try:
                payload = data.dict(exclude_none=True)
                if before_create:
                    payload = before_create(payload)
                r = supabase.table(table).insert(payload).execute()
                created = one_row(r)
                if created is None:
                    raise HTTPException(status_code=500, detail="Insert failed")
                return serialize_row(created)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"[{table}] create failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        async def update_item(item_id: str, data: update_model):
            # exclude_unset (not a None-filter): an explicitly-sent null clears the field,
            # an unset field is left untouched. See work_orders (backend edec24a).
            payload = data.dict(exclude_unset=True)
            if reject_empty_update and not payload:
                raise HTTPException(status_code=400, detail="No fields to update")
            if before_update:
                payload = before_update(payload)
            r = supabase.table(table).update(payload).eq("id", cast_id(item_id)).execute()
            updated = one_row(r)
            if updated is None:
                raise HTTPException(status_code=404, detail=not_found)
            return serialize_row(updated)

        async def delete_item(item_id: str):
            supabase.table(table).delete().eq("id", cast_id(item_id)).execute()
            return {"ok": True}

        # Register both "" and "/" to match the hand-written routers, which decorated both.
        for path in ("", "/"):
            self.router.add_api_route(
                path, list_items, methods=["GET"],
                dependencies=[Depends(get_current_user)],
            )
            self.router.add_api_route(
                path, create_item, methods=["POST"],
                dependencies=[Depends(get_current_user)],
            )
        self.router.add_api_route(
            "/{item_id}", update_item, methods=["PATCH"],
            dependencies=[Depends(get_current_user)],
        )
        self.router.add_api_route(
            "/{item_id}", delete_item, methods=["DELETE"],
            dependencies=[Depends(require_role("manager"))],
        )
