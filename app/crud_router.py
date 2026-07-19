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
- **POST ""** / **POST "/"** — insert `body.dict(exclude_none=True)`; 500 if the insert
  returns nothing; returns the created row. Requires a signed-in user.
- **PATCH "/{item_id}"** — update `body.dict(exclude_unset=True)` (explicit nulls clear a
  field, unset fields are left alone — the null-filter fix, backend edec24a); 404 if the
  row doesn't exist; returns the updated row. Requires a signed-in user.
- **DELETE "/{item_id}"** — delete by id; returns `{"ok": True}`. Requires manager+.

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
from typing import Optional, Type

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel

from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging

logger = logging.getLogger(__name__)


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

        async def list_items(request: Request):
            try:
                q = supabase.table(table).select("*")
                params = request.query_params
                search = params.get("search")
                if search and search_columns:
                    q = q.or_(",".join(f"{c}.ilike.%{search}%" for c in search_columns))
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
                    q = q.limit(limit)
                    offset = params.get("offset")
                    if offset:
                        q = q.offset(int(offset))
                rows = (q.execute()).data or []
                return [serialize_row(r) for r in rows]
            except Exception as e:
                logger.error(f"[{table}] list failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        async def create_item(data: create_model):
            try:
                payload = data.dict(exclude_none=True)
                r = supabase.table(table).insert(payload).execute()
                if not r.data:
                    raise HTTPException(status_code=500, detail="Insert failed")
                return serialize_row(r.data[0])
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"[{table}] create failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        async def update_item(item_id: int, data: update_model):
            # exclude_unset (not a None-filter): an explicitly-sent null clears the field,
            # an unset field is left untouched. See work_orders (backend edec24a).
            payload = data.dict(exclude_unset=True)
            if reject_empty_update and not payload:
                raise HTTPException(status_code=400, detail="No fields to update")
            r = supabase.table(table).update(payload).eq("id", item_id).execute()
            if not r.data:
                raise HTTPException(status_code=404, detail=not_found)
            return serialize_row(r.data[0])

        async def delete_item(item_id: int):
            supabase.table(table).delete().eq("id", item_id).execute()
            return {"ok": True}

        # Register both "" and "/" to match the hand-written routers, which decorated both.
        for path in ("", "/"):
            self.router.add_api_route(path, list_items, methods=["GET"])
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
