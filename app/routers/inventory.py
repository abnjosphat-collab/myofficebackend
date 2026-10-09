# backend/app/routers/inventory.py — the Inventory register, stored in the `inventory_items` table
# (supabase_migration_inventory_items.sql). Until 2026-10-09 this router held a module-level dict seeded
# with sample items and the page kept its items in each browser's localStorage, so nothing was shared or
# survived a cleared browser. The JSON shape (camelCase) is unchanged for the page; the table is snake_case.
# Stock status (in-stock / low-stock / out-of-stock) is computed on every read, never stored.

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user, require_role
from app.db_helpers import response_rows
from app.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

TABLE = "inventory_items"


class InventoryItem(BaseModel):
    id: str
    name: str
    sku: str
    category: str
    description: str
    currentStock: int
    minStock: int
    maxStock: int
    unit: str
    cost: float
    supplier: str
    location: str
    status: str
    lastRestocked: str
    createdAt: str
    updatedAt: str


class InventoryItemCreate(BaseModel):
    name: str
    sku: str = ""
    category: str = ""
    description: str = ""
    currentStock: int = 0
    minStock: int = 0
    maxStock: int = 0
    unit: str = ""
    cost: float = 0
    supplier: str = ""
    location: str = ""
    # Kept when a browser's old local list is moved up, so the restock date is not lost.
    lastRestocked: Optional[str] = None


class InventoryItemUpdate(BaseModel):
    name: Optional[str] = None
    sku: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    currentStock: Optional[int] = None
    minStock: Optional[int] = None
    maxStock: Optional[int] = None
    unit: Optional[str] = None
    cost: Optional[float] = None
    supplier: Optional[str] = None
    location: Optional[str] = None


# camelCase (API) <-> snake_case (table)
FIELDS = {
    "name": "name", "sku": "sku", "category": "category", "description": "description",
    "currentStock": "current_stock", "minStock": "min_stock", "maxStock": "max_stock", "unit": "unit",
    "cost": "cost", "supplier": "supplier", "location": "location", "lastRestocked": "last_restocked",
}


def calculate_status(current_stock: int, min_stock: int) -> str:
    if current_stock <= 0:
        return "out-of-stock"
    if current_stock <= min_stock:
        return "low-stock"
    return "in-stock"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _item(row: dict[str, Any]) -> InventoryItem:
    current, minimum = int(row.get("current_stock") or 0), int(row.get("min_stock") or 0)
    return InventoryItem(
        id=str(row["id"]), name=row.get("name") or "", sku=row.get("sku") or "", category=row.get("category") or "",
        description=row.get("description") or "", currentStock=current, minStock=minimum, maxStock=int(row.get("max_stock") or 0),
        unit=row.get("unit") or "", cost=float(row.get("cost") or 0), supplier=row.get("supplier") or "", location=row.get("location") or "",
        status=calculate_status(current, minimum), lastRestocked=str(row.get("last_restocked") or ""),
        createdAt=str(row.get("created_at") or ""), updatedAt=str(row.get("updated_at") or ""),
    )


def _all() -> List[InventoryItem]:
    try:
        rows = response_rows(supabase.table(TABLE).select("*").order("name").execute())
    except Exception as e:
        logger.error("inventory list failed: %s", e)
        raise HTTPException(status_code=502, detail="Inventory could not be read from the database.")
    return [_item(r) for r in rows]


def _row(item_id: str) -> dict[str, Any]:
    try:
        rows = response_rows(supabase.table(TABLE).select("*").eq("id", item_id).limit(1).execute())
    except Exception as e:
        logger.error("inventory read failed: %s", e)
        raise HTTPException(status_code=502, detail="The inventory item could not be read from the database.")
    if not rows:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return rows[0]


def _write(op: str, data: dict[str, Any], item_id: Optional[str] = None) -> InventoryItem:
    try:
        q = supabase.table(TABLE)
        result = response_rows((q.insert(data) if op == "insert" else q.update(data).eq("id", item_id)).execute())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"The inventory item was not saved: {e}")
    if not result:
        if op == "update":  # deleted by someone else since it was read
            raise HTTPException(status_code=404, detail="Inventory item not found; it may have just been deleted.")
        raise HTTPException(status_code=500, detail="The inventory item was not saved.")
    return _item(result[0])


@router.get("/items", response_model=List[InventoryItem], dependencies=[Depends(get_current_user)])
async def get_inventory_items(category: Optional[str] = None, status: Optional[str] = None, supplier: Optional[str] = None, search: Optional[str] = None):
    """All inventory items, optionally filtered."""
    items = _all()
    if category:
        items = [i for i in items if i.category == category]
    if status:
        items = [i for i in items if i.status == status]
    if supplier:
        items = [i for i in items if i.supplier == supplier]
    if search:
        s = search.lower()
        items = [i for i in items if s in i.name.lower() or s in i.sku.lower() or s in i.description.lower()]
    return items


@router.get("/items/{item_id}", response_model=InventoryItem, dependencies=[Depends(get_current_user)])
async def get_inventory_item(item_id: str):
    return _item(_row(item_id))


@router.post("/items", response_model=InventoryItem)
async def create_inventory_item(item: InventoryItemCreate, current_user: dict = Depends(get_current_user)):
    """Adds an item. Stock received now counts as restocked now, unless an earlier restock date is given."""
    if not item.name.strip():
        raise HTTPException(status_code=422, detail="An inventory item needs a name.")
    data = {FIELDS[k]: (v.strip() if isinstance(v, str) else v) for k, v in item.model_dump().items() if k in FIELDS and v is not None}
    if not item.lastRestocked:
        data["last_restocked"] = _now() if item.currentStock > 0 else None
    data["created_by"] = current_user.get("email")
    return _write("insert", data)


@router.put("/items/{item_id}", response_model=InventoryItem)
async def update_inventory_item(item_id: str, item_update: InventoryItemUpdate, current_user: dict = Depends(get_current_user)):
    """Changes the fields sent. Raising the stock records a restock."""
    existing = _row(item_id)
    sent = item_update.model_dump(exclude_unset=True)
    data = {FIELDS[k]: (v.strip() if isinstance(v, str) else v) for k, v in sent.items() if k in FIELDS}
    if "currentStock" in sent and sent["currentStock"] is not None and sent["currentStock"] > int(existing.get("current_stock") or 0):
        data["last_restocked"] = _now()
    data["updated_at"] = _now()
    return _write("update", data, item_id)


@router.delete("/items/{item_id}")
async def delete_inventory_item(item_id: str, current_user: dict = Depends(require_role("manager"))):
    _row(item_id)
    try:
        supabase.table(TABLE).delete().eq("id", item_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"The inventory item was not deleted: {e}")
    return {"message": "Inventory item deleted successfully"}


@router.post("/items/{item_id}/restock")
async def restock_item(item_id: str, quantity: int, current_user: dict = Depends(get_current_user)):
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")
    existing = _row(item_id)
    item = _write("update", {"current_stock": int(existing.get("current_stock") or 0) + quantity, "last_restocked": _now(), "updated_at": _now()}, item_id)
    return {"message": f"Restocked {quantity} units", "newStock": item.currentStock, "item": item}


@router.get("/stats", dependencies=[Depends(get_current_user)])
async def get_inventory_stats():
    items = _all()
    categories: dict[str, int] = {}
    for i in items:
        categories[i.category] = categories.get(i.category, 0) + 1
    return {
        "totalItems": len(items),
        "lowStock": sum(1 for i in items if i.status == "low-stock"),
        "outOfStock": sum(1 for i in items if i.status == "out-of-stock"),
        "totalValue": round(sum(i.currentStock * i.cost for i in items), 2),
        "categoryDistribution": categories,
    }


@router.get("/categories", dependencies=[Depends(get_current_user)])
async def get_categories():
    return {"categories": sorted({i.category for i in _all() if i.category})}


@router.get("/suppliers", dependencies=[Depends(get_current_user)])
async def get_suppliers():
    return {"suppliers": sorted({i.supplier for i in _all() if i.supplier})}


@router.get("/low-stock", dependencies=[Depends(get_current_user)])
async def get_low_stock_items():
    low = [i for i in _all() if i.status in ("low-stock", "out-of-stock")]
    return {"count": len(low), "items": low}
