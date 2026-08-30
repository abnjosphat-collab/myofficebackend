# tests/test_inventory_status.py — calculate_status() classifies stock level into
# out-of-stock/low-stock/in-stock, the status shown on every inventory item. Zero
# prior tests despite the boundary (current_stock exactly at min_stock) being the
# whole point of the function.

from app.routers.inventory import calculate_status


def test_zero_stock_is_out_of_stock():
    assert calculate_status(0, 5) == "out-of-stock"


def test_zero_stock_is_out_of_stock_even_with_zero_min():
    # current_stock == 0 short-circuits before the <= min_stock comparison.
    assert calculate_status(0, 0) == "out-of-stock"


def test_stock_exactly_at_minimum_is_low_stock():
    assert calculate_status(5, 5) == "low-stock"


def test_stock_below_minimum_is_low_stock():
    assert calculate_status(3, 5) == "low-stock"


def test_stock_above_minimum_is_in_stock():
    assert calculate_status(6, 5) == "in-stock"
