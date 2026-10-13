"""Which compressors exist, and what they are called, comes from the Equipment register.

A compressor is any equipment item whose category starts with "Compressors". The ``compressors`` table keeps only what the
register cannot hold: the operating status (running, standby, maintenance, offline) and the hour meters. Each tracked row
carries ``equipment_id``, the register item it belongs to. The register owns the unit's name, model and location, so a
rename in the Equipment register shows here, and a new compressor added there appears here without anyone re-entering it.

``sync_from_register`` does that housekeeping. It is idempotent, safe to call on every list request (it only writes when
something is missing or different), and never raises: the compressor list must still load from what it has stored if the
register cannot be read, and a failure is logged so it is not invisible.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EQUIPMENT_TABLE = "equipment"
COMPRESSORS_TABLE = "compressors"
#: Equipment categories that count as compressors (matched case-insensitively, as a prefix: "Compressors", "Compressor").
CATEGORY_PREFIX = "compressor"
_REGISTER_COLUMNS = "id,equipment_id,name,model,location,category,serial_number"
#: What a newly tracked compressor starts as; the operator sets the real state.
NEW_STATUS = "standby"


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def fields_from_register(unit: Dict[str, Any]) -> Dict[str, Any]:
    """The compressor columns the register owns, from one register item."""
    return {
        "name": _clean(unit.get("name")) or _clean(unit.get("equipment_id")) or f"Equipment {unit.get('id')}",
        "model": _clean(unit.get("model")),
        "location": _clean(unit.get("location")),
    }


def load_register_units(client) -> List[Dict[str, Any]]:
    """Every compressor in the Equipment register. Returns [] (and logs) when the register cannot be read."""
    try:
        result = client.table(EQUIPMENT_TABLE).select(_REGISTER_COLUMNS).ilike("category", f"{CATEGORY_PREFIX}%").execute()
        return list(result.data or [])
    except Exception as exc:  # noqa: BLE001 - the list must still load from the stored rows
        logger.warning("Could not read the Equipment register for compressors: %s", exc)
        return []


def with_register(compressors: List[Dict[str, Any]], units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Overlay the register's name, model and location, and add ``equipment_code`` (for example "CMP 01")."""
    by_id = {u.get("id"): u for u in units}
    for row in compressors:
        unit = by_id.get(row.get("equipment_id"))
        if unit:
            row.update(fields_from_register(unit))
            row["equipment_code"] = _clean(unit.get("equipment_id")) or None
        else:
            row["equipment_code"] = None
    return compressors


def sync_from_register(client, units: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Create a meter row for each register compressor that has none, and refresh stored copies that have drifted.

    Returns how many rows were created and updated. Never raises.
    """
    created = updated = 0
    try:
        units = load_register_units(client) if units is None else units
        if not units:
            return {"created": 0, "updated": 0}
        rows = client.table(COMPRESSORS_TABLE).select("id,name,model,location,equipment_id").execute().data or []
        tracked = {r.get("equipment_id"): r for r in rows if r.get("equipment_id") is not None}
        for unit in units:
            wanted = fields_from_register(unit)
            row = tracked.get(unit.get("id"))
            if row is None:
                try:
                    client.table(COMPRESSORS_TABLE).insert({
                        **wanted, "capacity": "", "status": NEW_STATUS, "equipment_id": unit["id"],
                        "serial_number": _clean(unit.get("serial_number")) or None,
                    }).execute()
                    created += 1
                except Exception as exc:  # noqa: BLE001 - a concurrent request may have created it first
                    logger.info("Compressor row for equipment %s was not created: %s", unit.get("id"), exc)
                continue
            drift = {k: v for k, v in wanted.items() if _clean(row.get(k)) != v}
            if drift:
                client.table(COMPRESSORS_TABLE).update(drift).eq("id", row["id"]).execute()
                updated += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("Compressor register sync failed: %s", exc)
    return {"created": created, "updated": updated}
