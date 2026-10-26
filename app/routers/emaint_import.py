# app/routers/emaint_import.py — M7: import eMaint assets and PM schedules.
#
# Three steps per kind, mirroring the spares import: the server reads the
# file (infer), matches every row to the register (preview), then writes
# only the rows that still come out new (commit — the server re-runs the
# whole decision, never trusting the browser's preview). Matched rows are
# always skipped, never overwritten, so re-running an import is safe.
import csv
import io
import logging
from datetime import datetime, time
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.audit import has_columns
from app.auth import get_current_user, require_role
from app.cache import invalidate_namespace
from app.clock import today_harare
from app.emaint_import import (
    ASSET_FIELDS,
    FREQUENCY_WORDS,
    MAX_IMPORT_ROWS,
    SCHEDULE_FIELDS,
    build_asset_insert,
    build_schedule_insert,
    extract_asset,
    extract_schedule,
    find_machine,
    flag_asset_duplicates,
    flag_schedule_duplicates,
    match_asset,
    match_headers,
)
from app.errors import server_error
from app.machine_history import schedule_matches
from app.routers.equipment import generate_equipment_id
from app.routers.schedules import next_occurrence
from app.scope import apply_list_scope, stamp_create
from app.supabase_client import supabase
from app.uploads import SPREADSHEET_EXTS, read_and_validate_upload

logger = logging.getLogger(__name__)
router = APIRouter()

# Manufacturer/Criticality ride along only once the register grows those
# columns (the Equipment model declares them; no migration has run yet).
HELD_COLUMNS = ("manufacturer", "criticality")


class ImportRows(BaseModel):
    """Raw spreadsheet rows plus the column mapping the user checked."""

    rows: list[dict[str, Any]]
    mapping: dict[str, Optional[str]]


def _rows_of(response: Any) -> list[dict[str, Any]]:
    return list(getattr(response, "data", response) or [])


def _safe_val(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, datetime) and value.time() == time.min:
        return value.date().isoformat()  # an Excel date cell previews as a date, not midnight
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _clean_headers(raw: list[Any]) -> list[str]:
    headers: list[str] = []
    seen: dict[str, int] = {}
    for i, header in enumerate(raw):
        name = str(header).strip() if header is not None else ""
        if not name or name.lower() == "none":
            name = f"Column_{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        headers.append(name)
    return headers


def _read_spreadsheet(content: bytes, filename: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Headers + data rows (each carrying its `_row` file row number) from an
    upload. CSV/TSV via the csv module (exact row numbers, BOM-tolerant,
    cp1252 fallback for Excel-saved files); xlsx via openpyxl. Blank rows
    are skipped. Raises 400 for an unreadable or empty file."""
    name = (filename or "").lower()
    headers: list[str] = []
    records: list[dict[str, Any]] = []
    if name.endswith(".csv") or name.endswith(".tsv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1252")
        reader = csv.DictReader(io.StringIO(text), delimiter="\t" if name.endswith(".tsv") else ",",
                                restval="")
        headers = _clean_headers(list(reader.fieldnames or []))
        for record in reader:
            if record is None:
                continue
            values = [record.get(h) for h in (reader.fieldnames or [])]
            if not any(v is not None and str(v).strip() for v in values):
                continue  # fully blank line — skipped, numbering stays true
            row = {headers[i]: _safe_val(record.get(h)) for i, h in enumerate(reader.fieldnames or [])}
            row["_row"] = reader.line_num
            records.append(row)
    else:
        try:
            import openpyxl as _oxl

            book = _oxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
            sheet = book.active
            rows = list(sheet.iter_rows(values_only=True))
            book.close()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="The file could not be read as an Excel workbook (for .xls, re-save as .xlsx or .csv first)")
        if not rows:
            raise HTTPException(status_code=400, detail="The file appears to be empty")
        headers = _clean_headers(list(rows[0]))
        for lineno, values in enumerate(rows[1:], start=2):
            if not any(v is not None and str(v).strip() for v in (values or ())):
                continue
            row = {headers[i]: _safe_val(values[i] if i < len(values) else None) for i in range(len(headers))}
            row["_row"] = lineno
            records.append(row)
    if not records:
        raise HTTPException(status_code=400, detail="The file contains no data rows")
    if len(records) > MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"The file holds {len(records)} rows, above the {MAX_IMPORT_ROWS}-row import limit — split it and import each part")
    # Drop columns that are entirely blank (they match nothing and clutter mapping).
    live = [h for h in headers if any(r.get(h) not in (None, "") for r in records)]
    for row in records:
        for header in headers:
            if header not in live:
                row.pop(header, None)
    return live, records


async def _infer(file: UploadFile, fields: dict) -> dict[str, Any]:
    content = await read_and_validate_upload(file, max_bytes=25 * 1024 * 1024, allowed_exts=SPREADSHEET_EXTS)
    columns, raw_rows = _read_spreadsheet(content, file.filename or "")
    inferred, confidence = match_headers(columns, fields)
    return {"inferred": inferred, "confidence": confidence, "all_columns": columns,
            "raw_rows": raw_rows, "total_rows": len(raw_rows)}


def _register(user: dict) -> list[dict[str, Any]]:
    query = apply_list_scope(supabase.table("equipment").select("*"), user, "equipment")
    return _rows_of(query.execute())


def _schedules(user: dict) -> list[dict[str, Any]]:
    query = apply_list_scope(supabase.table("maintenance_schedules").select("*"), user, "maintenance_schedules")
    return _rows_of(query.execute())


def _check_mapping(mapping: dict[str, Optional[str]], required: dict[str, str]) -> None:
    missing = [label for key, label in required.items() if not mapping.get(key)]
    if missing:
        raise HTTPException(status_code=422, detail=f"Choose the {', '.join(missing)} column{'s' if len(missing) > 1 else ''} first")


def _check_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise HTTPException(status_code=422, detail="No rows were sent")
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(status_code=400, detail=f"{len(rows)} rows is above the {MAX_IMPORT_ROWS}-row import limit")


def _preview_assets(rows: list[dict[str, Any]], mapping: dict[str, Optional[str]],
                    register: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for row in rows:
        values, errors = extract_asset(row, mapping)
        preview: dict[str, Any] = {"row": row.get("_row"), "ref": values.get("asset_id") or "",
                                   "values": values, "errors": errors, "match": None, "note": ""}
        if errors:
            preview["status"] = "invalid"
        else:
            status, hit, note = match_asset(values, register)
            preview["status"] = status
            preview["note"] = note
            if hit is not None:
                preview["match"] = {"id": hit.get("id"), "name": hit.get("name"),
                                    "equipment_id": hit.get("equipment_id")}
        previews.append(preview)
    flag_asset_duplicates(previews)
    return previews


def _preview_schedules(rows: list[dict[str, Any]], mapping: dict[str, Optional[str]],
                       register: list[dict[str, Any]], schedules: list[dict[str, Any]],
                       links_active: bool) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for row in rows:
        values, errors = extract_schedule(row, mapping)
        preview: dict[str, Any] = {"row": row.get("_row"), "ref": (values.get("ref") or {}).get("pm_id") or "",
                                   "values": values, "errors": list(errors), "match": None, "note": ""}
        if not errors:
            machine, hint = find_machine(values["asset_id"], values["asset_name"], register)
            if machine is None:
                if hint is not None:
                    preview["errors"].append(
                        f"No register machine '{values['asset_id'] or values['asset_name']}' "
                        f"(closest: '{hint.get('name')}' [{hint.get('equipment_id') or 'no code'}] — fix the spelling first)")
                else:
                    preview["errors"].append(
                        f"No register machine '{values['asset_id'] or values['asset_name']}' "
                        "(import the asset first, or fix the spelling)")
            else:
                name_fold = str(machine.get("name") or "").strip().casefold()
                title_fold = str(values.get("title") or "").strip().casefold()
                dup = next((s for s in schedules
                            if schedule_matches(s, machine.get("id"), name_fold, links_active)
                            and str(s.get("name") or "").strip().casefold() == title_fold), None)
                if dup is not None:
                    preview["status"] = "exists"
                    preview["note"] = f"'{values['title']}' already scheduled for {machine.get('name')}"
                    preview["match"] = {"id": dup.get("id"), "name": dup.get("name")}
                else:
                    preview["status"] = "new"
                    preview["match"] = {"id": machine.get("id"), "name": machine.get("name"),
                                        "equipment_id": machine.get("equipment_id")}
        if preview.get("errors"):
            preview["status"] = "invalid"
        previews.append(preview)
    flag_schedule_duplicates(previews)
    return previews


def _summary(previews: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"new": 0, "exists": 0, "close": 0, "invalid": 0}
    for preview in previews:
        counts[preview.get("status", "invalid")] = counts.get(preview.get("status", "invalid"), 0) + 1
    return counts


@router.post("/assets/infer")
async def infer_asset_columns(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """Read an asset spreadsheet and work out which column is which eMaint field."""
    try:
        return await _infer(file, ASSET_FIELDS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"eMaint asset infer failed: {e}")
        raise server_error(e, "Reading the asset file")


@router.post("/assets/preview")
async def preview_assets(payload: ImportRows, current_user: dict = Depends(get_current_user)):
    """Match asset rows to the register: new, already there, near miss, or invalid."""
    try:
        _check_mapping(payload.mapping, {"name": "Asset Description"})
        _check_rows(payload.rows)
        register = _register(current_user)
        previews = _preview_assets(payload.rows, payload.mapping, register)
        held_live = [c for c in HELD_COLUMNS if has_columns(supabase, "equipment", [c])]
        return {"rows": previews, "summary": _summary(previews), "held_columns": held_live,
                "register_size": len(register)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"eMaint asset preview failed: {e}")
        raise server_error(e, "Matching the assets")


@router.post("/assets/commit")
async def commit_assets(payload: ImportRows, current_user: dict = Depends(get_current_user)):
    """Create the register rows for asset rows that still come out new.

    Every row is extracted and matched again here: the preview is a courtesy,
    this endpoint is the rule. Matched and invalid rows are skipped with
    their reasons; one failed insert never stops the rest."""
    try:
        _check_mapping(payload.mapping, {"name": "Asset Description"})
        _check_rows(payload.rows)
        register = _register(current_user)
        previews = _preview_assets(payload.rows, payload.mapping, register)
        extra = {c for c in HELD_COLUMNS if has_columns(supabase, "equipment", [c])}
        created = skipped = errors = 0
        report: list[dict[str, Any]] = []
        for preview in previews:
            if preview["status"] != "new":
                skipped += 1
                report.append({"row": preview["row"], "ref": preview["ref"], "status": preview["status"],
                               "id": None, "reasons": preview["errors"] or [preview["note"]]})
                continue
            try:
                insert = build_asset_insert(preview["values"], extra)
                if not insert.get("equipment_id"):
                    insert["equipment_id"] = generate_equipment_id()
                insert = stamp_create(current_user, "equipment", insert)
                saved = _rows_of(supabase.table("equipment").insert(insert).execute())
                if not saved:
                    raise RuntimeError("no row returned")
                created += 1
                report.append({"row": preview["row"], "ref": preview["ref"], "status": "created",
                               "id": saved[0].get("id"), "reasons": []})
            except Exception as row_error:  # noqa: BLE001 — one bad row never stops the file
                logger.error(f"eMaint asset row {preview['row']} failed: {row_error}")
                errors += 1
                report.append({"row": preview["row"], "ref": preview["ref"], "status": "error",
                               "id": None, "reasons": ["Could not be saved; the rest of the file was imported"]})
        if created:
            await invalidate_namespace("equipment")
        return {"created": created, "skipped": skipped, "errors": errors,
                "total": len(previews), "rows": report}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"eMaint asset commit failed: {e}")
        raise server_error(e, "Importing the assets")


@router.post("/schedules/infer")
async def infer_schedule_columns(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """Read a PM spreadsheet and work out which column is which eMaint field."""
    try:
        return await _infer(file, SCHEDULE_FIELDS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"eMaint schedule infer failed: {e}")
        raise server_error(e, "Reading the PM file")


@router.post("/schedules/preview")
async def preview_schedules(payload: ImportRows, current_user: dict = Depends(get_current_user)):
    """Match PM rows to register machines and existing schedules."""
    try:
        _check_mapping(payload.mapping, {"title": "PM Description", "frequency": "Frequency"})
        if not payload.mapping.get("asset_id") and not payload.mapping.get("asset_name"):
            raise HTTPException(status_code=422, detail="Choose the Asset ID or Asset Description column first")
        _check_rows(payload.rows)
        register = _register(current_user)
        schedules = _schedules(current_user)
        links_active = has_columns(supabase, "maintenance_schedules", ["equipment_id"])
        previews = _preview_schedules(payload.rows, payload.mapping, register, schedules, links_active)
        return {"rows": previews, "summary": _summary(previews), "links_active": links_active,
                "frequencies": FREQUENCY_WORDS}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"eMaint schedule preview failed: {e}")
        raise server_error(e, "Matching the PM schedules")


@router.post("/schedules/commit")
async def commit_schedules(payload: ImportRows, current_user: dict = Depends(require_role("manager"))):
    """Create the schedules for PM rows that still come out new (manager only,
    like every schedule write). Re-matches every row; skips the rest with reasons."""
    try:
        _check_mapping(payload.mapping, {"title": "PM Description", "frequency": "Frequency"})
        if not payload.mapping.get("asset_id") and not payload.mapping.get("asset_name"):
            raise HTTPException(status_code=422, detail="Choose the Asset ID or Asset Description column first")
        _check_rows(payload.rows)
        register = _register(current_user)
        schedules = _schedules(current_user)
        links_active = has_columns(supabase, "maintenance_schedules", ["equipment_id"])
        previews = _preview_schedules(payload.rows, payload.mapping, register, schedules, links_active)
        created = skipped = errors = 0
        report: list[dict[str, Any]] = []
        for preview in previews:
            if preview["status"] != "new":
                skipped += 1
                report.append({"row": preview["row"], "ref": preview["ref"], "status": preview["status"],
                               "id": None, "reasons": preview["errors"] or [preview["note"]]})
                continue
            try:
                machine = next(r for r in register if r.get("id") == (preview["match"] or {}).get("id"))
                insert = build_schedule_insert(preview["values"], machine, links_active)
                insert["created_by"] = current_user["user_id"]
                insert = stamp_create(current_user, "maintenance_schedules", insert)
                if not insert.get("next_due_date"):
                    insert["next_due_date"] = next_occurrence(insert, today_harare()).isoformat()
                saved = _rows_of(supabase.table("maintenance_schedules").insert(insert).execute())
                if not saved:
                    raise RuntimeError("no row returned")
                created += 1
                report.append({"row": preview["row"], "ref": preview["ref"], "status": "created",
                               "id": saved[0].get("id"), "reasons": []})
            except Exception as row_error:  # noqa: BLE001 — one bad row never stops the file
                logger.error(f"eMaint schedule row {preview['row']} failed: {row_error}")
                errors += 1
                report.append({"row": preview["row"], "ref": preview["ref"], "status": "error",
                               "id": None, "reasons": ["Could not be saved; the rest of the file was imported"]})
        return {"created": created, "skipped": skipped, "errors": errors,
                "total": len(previews), "rows": report}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"eMaint schedule commit failed: {e}")
        raise server_error(e, "Importing the PM schedules")
