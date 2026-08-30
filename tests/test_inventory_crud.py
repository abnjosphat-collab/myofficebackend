# tests/test_inventory_crud.py — the ten handlers left uncovered after
# test_inventory_status.py (calculate_status's boundary behavior): get_inventory_items,
# get_inventory_item, create_inventory_item, update_inventory_item,
# delete_inventory_item, restock_item, get_inventory_stats, get_categories,
# get_suppliers, get_low_stock_items.
#
# inventory.py has no Supabase table at all — it's a plain module-level dict
# (`inventory_db`, seeded once at import by init_sample_data()), so there's no fake
# client to patch here. Each test resets `inventory_db` to a known, controlled set of
# items via an autouse fixture instead.
#
# Bug found + fixed in app/routers/inventory.py's update_inventory_item: the
# "bump lastRestocked when a PUT raises currentStock" check compared
# update_data['currentStock'] against existing_item.get('previous_stock', ...) — but
# 'previous_stock' was never a real key, and by the time that line ran the update loop
# above it had already overwritten existing_item['currentStock'] with the *new* value,
# so the default made the comparison `new > new`, always False. lastRestocked could
# never actually bump via this endpoint. Fixed by capturing the pre-update stock before
# the loop runs. test_update_currentStock_increase_bumps_last_restocked and
# test_update_currentStock_decrease_does_not_bump_last_restocked below pin the
# corrected behavior.

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

import app.routers.inventory as inv_mod
from app.routers.inventory import (
    InventoryItemCreate, InventoryItemUpdate,
    get_inventory_items, get_inventory_item, create_inventory_item,
    update_inventory_item, delete_inventory_item, restock_item,
    get_inventory_stats, get_categories, get_suppliers, get_low_stock_items,
)


@pytest.fixture(autouse=True)
def reset_inventory_db():
    inv_mod.inventory_db.clear()
    yield
    inv_mod.inventory_db.clear()


def _item(**overrides):
    now = datetime.now().isoformat()
    base = {
        "id": "inv-001", "name": "Widget", "sku": "W-1", "category": "Tools",
        "description": "A widget", "currentStock": 10, "minStock": 5, "maxStock": 50,
        "unit": "pcs", "cost": 2.5, "supplier": "Acme", "location": "A1",
        "status": "in-stock", "lastRestocked": now, "createdAt": now, "updatedAt": now,
    }
    base.update(overrides)
    return base


def _seed(*items):
    for item in items:
        inv_mod.inventory_db[item["id"]] = item


# ─── get_inventory_items ──────────────────────────────────────────────────────────────

async def test_get_items_with_no_filters_returns_everything():
    _seed(_item(id="inv-001"), _item(id="inv-002", name="Gadget"))
    result = await get_inventory_items(category=None, status=None, supplier=None, search=None)
    assert len(result) == 2


async def test_get_items_filters_by_category():
    _seed(
        _item(id="inv-001", category="Tools"),
        _item(id="inv-002", category="Electronics"),
    )
    result = await get_inventory_items(category="Electronics", status=None, supplier=None, search=None)
    assert [i["id"] for i in result] == ["inv-002"]


async def test_get_items_filters_by_status():
    _seed(
        _item(id="inv-001", status="in-stock"),
        _item(id="inv-002", status="low-stock"),
    )
    result = await get_inventory_items(category=None, status="low-stock", supplier=None, search=None)
    assert [i["id"] for i in result] == ["inv-002"]


async def test_get_items_filters_by_supplier():
    _seed(
        _item(id="inv-001", supplier="Acme"),
        _item(id="inv-002", supplier="Globex"),
    )
    result = await get_inventory_items(category=None, status=None, supplier="Globex", search=None)
    assert [i["id"] for i in result] == ["inv-002"]


async def test_get_items_search_matches_name_sku_or_description():
    _seed(
        _item(id="inv-001", name="Circuit Board", sku="CB-1", description="electronics part"),
        _item(id="inv-002", name="Safety Gloves", sku="SG-1", description="hand protection"),
    )
    by_name = await get_inventory_items(category=None, status=None, supplier=None, search="circuit")
    assert [i["id"] for i in by_name] == ["inv-001"]

    by_sku = await get_inventory_items(category=None, status=None, supplier=None, search="sg-1")
    assert [i["id"] for i in by_sku] == ["inv-002"]

    by_description = await get_inventory_items(category=None, status=None, supplier=None, search="protection")
    assert [i["id"] for i in by_description] == ["inv-002"]


async def test_get_items_combined_filters_intersect():
    _seed(
        _item(id="inv-001", category="Tools", status="in-stock"),
        _item(id="inv-002", category="Tools", status="low-stock"),
        _item(id="inv-003", category="Electronics", status="low-stock"),
    )
    result = await get_inventory_items(category="Tools", status="low-stock", supplier=None, search=None)
    assert [i["id"] for i in result] == ["inv-002"]


# ─── get_inventory_item ──────────────────────────────────────────────────────────────

async def test_get_item_found():
    _seed(_item(id="inv-001", name="Widget"))
    result = await get_inventory_item("inv-001")
    assert result["name"] == "Widget"


async def test_get_item_not_found_is_404():
    with pytest.raises(HTTPException) as exc:
        await get_inventory_item("missing")
    assert exc.value.status_code == 404


# ─── create_inventory_item ───────────────────────────────────────────────────────────

async def test_create_item_assigns_sequential_id_and_computed_status():
    _seed(_item(id="inv-001"))  # one pre-existing item
    payload = InventoryItemCreate(
        name="New Gadget", sku="NG-1", category="Electronics", description="desc",
        currentStock=2, minStock=10, maxStock=50, unit="pcs", cost=5.0,
        supplier="Acme", location="B2",
    )
    result = await create_inventory_item(payload, current_user={"user_id": "u1"})
    assert result.id == "inv-002"
    assert result.status == "low-stock"  # calculate_status(2, 10)
    assert inv_mod.inventory_db["inv-002"]["name"] == "New Gadget"


async def test_create_item_with_positive_stock_sets_last_restocked_to_now():
    payload = InventoryItemCreate(
        name="Widget", sku="W-1", category="Tools", description="desc",
        currentStock=10, minStock=5, maxStock=50, unit="pcs", cost=1.0,
        supplier="Acme", location="A1",
    )
    before = datetime.now()
    result = await create_inventory_item(payload, current_user={"user_id": "u1"})
    last_restocked = datetime.fromisoformat(result.lastRestocked)
    assert last_restocked >= before


async def test_create_item_with_zero_stock_backdates_last_restocked():
    payload = InventoryItemCreate(
        name="Widget", sku="W-1", category="Tools", description="desc",
        currentStock=0, minStock=5, maxStock=50, unit="pcs", cost=1.0,
        supplier="Acme", location="A1",
    )
    result = await create_inventory_item(payload, current_user={"user_id": "u1"})
    last_restocked = datetime.fromisoformat(result.lastRestocked)
    # Backdated ~30 days, definitely more than a day in the past.
    assert last_restocked < datetime.now() - timedelta(days=25)
    assert result.status == "out-of-stock"


# ─── update_inventory_item ───────────────────────────────────────────────────────────

async def test_update_item_not_found_is_404():
    with pytest.raises(HTTPException) as exc:
        await update_inventory_item("missing", InventoryItemUpdate(name="X"))
    assert exc.value.status_code == 404


async def test_update_item_only_touches_explicitly_sent_fields():
    _seed(_item(id="inv-001", name="Widget", location="A1"))
    result = await update_inventory_item("inv-001", InventoryItemUpdate(name="Renamed"))
    assert result["name"] == "Renamed"
    assert result["location"] == "A1"  # untouched


async def test_update_item_explicit_null_clears_the_field():
    _seed(_item(id="inv-001", description="original description"))
    result = await update_inventory_item("inv-001", InventoryItemUpdate(description=None))
    assert "description" in InventoryItemUpdate(description=None).dict(exclude_unset=True)
    assert result["description"] is None


async def test_update_item_recalculates_status_when_stock_changes():
    _seed(_item(id="inv-001", currentStock=10, minStock=5, status="in-stock"))
    result = await update_inventory_item("inv-001", InventoryItemUpdate(currentStock=3))
    assert result["status"] == "low-stock"


async def test_update_currentStock_increase_bumps_last_restocked():
    old_timestamp = (datetime.now() - timedelta(days=10)).isoformat()
    _seed(_item(id="inv-001", currentStock=5, minStock=5, lastRestocked=old_timestamp))
    before = datetime.now()
    result = await update_inventory_item("inv-001", InventoryItemUpdate(currentStock=20))
    assert datetime.fromisoformat(result["lastRestocked"]) >= before


async def test_update_currentStock_decrease_does_not_bump_last_restocked():
    old_timestamp = (datetime.now() - timedelta(days=10)).isoformat()
    _seed(_item(id="inv-001", currentStock=20, minStock=5, lastRestocked=old_timestamp))
    result = await update_inventory_item("inv-001", InventoryItemUpdate(currentStock=5))
    assert result["lastRestocked"] == old_timestamp


# ─── delete_inventory_item ───────────────────────────────────────────────────────────

async def test_delete_item_not_found_is_404():
    with pytest.raises(HTTPException) as exc:
        await delete_inventory_item("missing", current_user={"user_id": "u1", "role": "manager"})
    assert exc.value.status_code == 404


async def test_delete_item_removes_it():
    _seed(_item(id="inv-001"))
    result = await delete_inventory_item("inv-001", current_user={"user_id": "u1", "role": "manager"})
    assert result == {"message": "Inventory item deleted successfully"}
    assert "inv-001" not in inv_mod.inventory_db


# ─── restock_item ────────────────────────────────────────────────────────────────────

async def test_restock_not_found_is_404():
    with pytest.raises(HTTPException) as exc:
        await restock_item("missing", 5, current_user={"user_id": "u1"})
    assert exc.value.status_code == 404


@pytest.mark.parametrize("quantity", [0, -5])
async def test_restock_non_positive_quantity_is_400(quantity):
    _seed(_item(id="inv-001"))
    with pytest.raises(HTTPException) as exc:
        await restock_item("inv-001", quantity, current_user={"user_id": "u1"})
    assert exc.value.status_code == 400


async def test_restock_increments_stock_and_recalculates_status():
    _seed(_item(id="inv-001", currentStock=0, minStock=15, status="out-of-stock"))
    result = await restock_item("inv-001", 3, current_user={"user_id": "u1"})
    assert result["newStock"] == 3
    assert result["item"]["status"] == "low-stock"  # calculate_status(3, 15)
    assert result["message"] == "Restocked 3 units"


async def test_restock_crossing_above_min_stock_sets_in_stock():
    _seed(_item(id="inv-001", currentStock=45, minStock=20, status="in-stock"))
    result = await restock_item("inv-001", 10, current_user={"user_id": "u1"})
    assert result["newStock"] == 55
    assert result["item"]["status"] == "in-stock"


async def test_restock_updates_last_restocked_timestamp():
    old_timestamp = (datetime.now() - timedelta(days=10)).isoformat()
    _seed(_item(id="inv-001", currentStock=1, minStock=5, lastRestocked=old_timestamp))
    before = datetime.now()
    result = await restock_item("inv-001", 5, current_user={"user_id": "u1"})
    assert datetime.fromisoformat(result["item"]["lastRestocked"]) >= before


# ─── get_inventory_stats ─────────────────────────────────────────────────────────────

async def test_stats_computes_counts_value_and_category_distribution():
    _seed(
        _item(id="inv-001", category="Tools", status="in-stock", currentStock=10, cost=2.0),
        _item(id="inv-002", category="Tools", status="low-stock", currentStock=3, cost=5.0),
        _item(id="inv-003", category="Electronics", status="out-of-stock", currentStock=0, cost=100.0),
    )
    stats = await get_inventory_stats()
    assert stats["totalItems"] == 3
    assert stats["lowStock"] == 1
    assert stats["outOfStock"] == 1
    assert stats["totalValue"] == 10 * 2.0 + 3 * 5.0 + 0 * 100.0
    assert stats["categoryDistribution"] == {"Tools": 2, "Electronics": 1}


async def test_stats_on_empty_inventory():
    stats = await get_inventory_stats()
    assert stats == {
        "totalItems": 0, "lowStock": 0, "outOfStock": 0,
        "totalValue": 0, "categoryDistribution": {},
    }


# ─── get_categories / get_suppliers ──────────────────────────────────────────────────

async def test_categories_are_sorted_and_deduplicated():
    _seed(
        _item(id="inv-001", category="Tools"),
        _item(id="inv-002", category="Electronics"),
        _item(id="inv-003", category="Tools"),
    )
    result = await get_categories()
    assert result == {"categories": ["Electronics", "Tools"]}


async def test_suppliers_are_sorted_and_deduplicated():
    _seed(
        _item(id="inv-001", supplier="Zeta Co"),
        _item(id="inv-002", supplier="Acme"),
        _item(id="inv-003", supplier="Acme"),
    )
    result = await get_suppliers()
    assert result == {"suppliers": ["Acme", "Zeta Co"]}


# ─── get_low_stock_items ─────────────────────────────────────────────────────────────

async def test_low_stock_items_includes_low_and_out_of_stock_only():
    _seed(
        _item(id="inv-001", status="in-stock"),
        _item(id="inv-002", status="low-stock"),
        _item(id="inv-003", status="out-of-stock"),
    )
    result = await get_low_stock_items()
    assert result["count"] == 2
    assert {i["id"] for i in result["items"]} == {"inv-002", "inv-003"}


async def test_low_stock_items_empty_when_all_in_stock():
    _seed(_item(id="inv-001", status="in-stock"))
    result = await get_low_stock_items()
    assert result == {"count": 0, "items": []}
