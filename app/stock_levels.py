"""Stock levels — taking issued items off a spare's quantity, and putting them back.

Issuing stock (Stock Issues) reduces the spare's `current_quantity` in the Spares register; deleting an issue puts the
quantity back; editing an issue's items adjusts by the difference. The spare is found by its stock code. An item with no
stock code, or a code that is not in the register, is reported and skipped (the issue itself is still recorded). A quantity
is never taken below zero: if more is issued than the register holds, the spare goes to zero and the shortfall is reported.

`current_quantity` is a whole number in the database, so a fractional issue (for example 2.5 m of cable) is rounded to
the nearest whole unit when it is taken off.

The update is conditional on the quantity read, and retried a few times, so two people issuing the same part at once do not
overwrite each other.
"""
import logging
from typing import Any, Iterable

from app.supabase_client import one_row, supabase

logger = logging.getLogger(__name__)

SPARES = "spares"
ATTEMPTS = 4


def item_deltas(items: Iterable[dict[str, Any]] | None) -> dict[str, float]:
    """Quantity issued per stock code (several lines for one code add up). Lines without a stock code or quantity are left out."""
    deltas: dict[str, float] = {}
    for item in items or []:
        code = (item.get("stock_code") or "").strip()
        try:
            qty = float(item.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if code and qty:
            deltas[code] = deltas.get(code, 0) + qty
    return deltas


def adjust_stock(deltas: dict[str, float], *, issue: bool = True) -> dict[str, Any]:
    """Take each quantity off its spare (`issue=True`) or put it back (`issue=False`).

    Returns {"stock": [{stock_code, before, after, short_by}], "warnings": [str]}. Never raises for a missing spare or a
    shortfall; a database failure on one spare is reported as a warning and the others are still applied.
    """
    updates: list[dict[str, Any]] = []
    warnings: list[str] = []
    for code, qty in deltas.items():
        change = -qty if issue else qty
        try:
            result = _apply(code, change)
        except Exception as e:  # one spare failing must not hide the others
            logger.error(f"stock adjust failed for {code}: {e}")
            warnings.append(f"{code}: the stock quantity could not be updated ({e}).")
            continue
        if result is None:
            warnings.append(f"{code}: not found in the spares register, so no stock was changed.")
            continue
        updates.append(result)
        if result["short_by"]:
            warnings.append(f"{code}: only {result['before']} in stock, so it is now 0 ({result['short_by']:g} short).")
    return {"stock": updates, "warnings": warnings}


def _apply(code: str, change: float) -> dict[str, Any] | None:
    for _ in range(ATTEMPTS):
        row = one_row(supabase.table(SPARES).select("id,current_quantity").eq("stock_code", code).limit(1).execute())
        if row is None:
            return None
        before = int(row.get("current_quantity") or 0)
        wanted = before + change
        after = max(0, int(round(wanted)))
        short_by = max(0.0, -wanted) if wanted < 0 else 0.0
        updated = one_row(supabase.table(SPARES).update({"current_quantity": after}).eq("id", row["id"]).eq("current_quantity", before).execute())
        if updated is not None:
            return {"stock_code": code, "before": before, "after": after, "short_by": short_by}
    raise RuntimeError("the quantity kept changing underneath this update")
