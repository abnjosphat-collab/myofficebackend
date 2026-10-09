# tests/test_inventory_crud.py — the inventory register's route handlers against an in-memory stand-in for the
# `inventory_items` table. Until 2026-10-09 the router held a sample-seeded dict; these tests pin the
# database-backed behaviour: items persist through the table, the API keeps its camelCase shape, stock status is
# computed on read, raising stock records a restock, and a database failure is reported, never an empty register.

import asyncio

import pytest
from fastapi import HTTPException

import app.routers.inventory as inv_mod
from app.routers.inventory import (
    InventoryItemCreate, InventoryItemUpdate, create_inventory_item, delete_inventory_item, get_categories,
    get_inventory_item, get_inventory_items, get_inventory_stats, get_low_stock_items, get_suppliers, restock_item,
    update_inventory_item,
)
from tests._table_fake import TableFake

USER = {"user_id": "u1", "email": "u1@x.com", "role": "user"}
MANAGER = {"user_id": "m1", "email": "m1@x.com", "role": "manager"}


@pytest.fixture
def db(monkeypatch):
    fake = TableFake()
    monkeypatch.setattr(inv_mod, "supabase", fake)
    return fake


def run(coro):
    return asyncio.run(coro)


def add(name="Safety gloves", stock=10, minimum=5, cost=2.5, category="PPE", supplier="Acme", **extra):
    return run(create_inventory_item(InventoryItemCreate(
        name=name, sku=f"SKU-{name[:3].upper()}", category=category, description="", currentStock=stock, minStock=minimum,
        maxStock=50, unit="pair", cost=cost, supplier=supplier, location="Store A", **extra), current_user=USER))


def test_an_item_is_stored_in_snake_case_and_returned_in_the_page_shape(db):
    item = add()
    row = db.tables["inventory_items"][0]
    assert row["current_stock"] == 10 and row["min_stock"] == 5 and row["name"] == "Safety gloves"
    assert item.currentStock == 10 and item.status == "in-stock" and item.lastRestocked


def test_there_are_no_sample_items_when_nothing_was_entered(db):
    assert run(get_inventory_items()) == []


def test_status_is_computed_from_stock_on_read(db):
    add(name="Gloves", stock=0)
    add(name="Masks", stock=3, minimum=5)
    add(name="Boots", stock=20, minimum=5)
    assert {i.name: i.status for i in run(get_inventory_items())} == {"Gloves": "out-of-stock", "Masks": "low-stock", "Boots": "in-stock"}


def test_a_moved_up_item_keeps_its_restock_date(db):
    item = add(lastRestocked="2026-01-05T00:00:00+00:00")
    assert item.lastRestocked == "2026-01-05T00:00:00+00:00"


def test_a_nameless_item_is_refused(db):
    with pytest.raises(HTTPException) as err:
        add(name="   ")
    assert err.value.status_code == 422


def test_raising_stock_records_a_restock_and_lowering_does_not(db):
    item = add(stock=10)
    db.tables["inventory_items"][0]["last_restocked"] = "2026-01-01T00:00:00+00:00"
    lowered = run(update_inventory_item(item.id, InventoryItemUpdate(currentStock=4), current_user=USER))
    assert lowered.lastRestocked == "2026-01-01T00:00:00+00:00" and lowered.status == "low-stock"
    raised = run(update_inventory_item(item.id, InventoryItemUpdate(currentStock=30), current_user=USER))
    assert raised.lastRestocked != "2026-01-01T00:00:00+00:00" and raised.status == "in-stock"


def test_update_changes_only_the_fields_sent(db):
    item = add()
    updated = run(update_inventory_item(item.id, InventoryItemUpdate(location="Store B"), current_user=USER))
    assert updated.location == "Store B" and updated.name == "Safety gloves" and updated.currentStock == 10


def test_restock_adds_to_the_stock(db):
    item = add(stock=2)
    result = run(restock_item(item.id, 8, current_user=USER))
    assert result["newStock"] == 10
    with pytest.raises(HTTPException) as err:
        run(restock_item(item.id, 0, current_user=USER))
    assert err.value.status_code == 400


def test_delete_removes_the_item_and_a_missing_one_is_404(db):
    item = add()
    run(delete_inventory_item(item.id, current_user=MANAGER))
    assert db.tables["inventory_items"] == []
    with pytest.raises(HTTPException) as err:
        run(get_inventory_item(item.id))
    assert err.value.status_code == 404


def test_filters_and_summaries_come_from_the_stored_items(db):
    add(name="Gloves", stock=0, cost=2, category="PPE", supplier="Acme")
    add(name="Grease", stock=4, minimum=5, cost=10, category="Lubricants", supplier="Oilco")
    add(name="Boots", stock=10, cost=30, category="PPE", supplier="Acme")
    assert [i.name for i in run(get_inventory_items(category="PPE"))] == ["Boots", "Gloves"]
    assert [i.name for i in run(get_inventory_items(search="gre"))] == ["Grease"]
    stats = run(get_inventory_stats())
    assert stats == {"totalItems": 3, "lowStock": 1, "outOfStock": 1, "totalValue": 340.0, "categoryDistribution": {"Lubricants": 1, "PPE": 2}}
    assert run(get_categories()) == {"categories": ["Lubricants", "PPE"]}
    assert run(get_suppliers()) == {"suppliers": ["Acme", "Oilco"]}
    assert run(get_low_stock_items())["count"] == 2


def test_a_database_failure_is_reported_not_shown_as_an_empty_register(db):
    db.fail = True
    with pytest.raises(HTTPException) as err:
        run(get_inventory_items())
    assert err.value.status_code == 502
