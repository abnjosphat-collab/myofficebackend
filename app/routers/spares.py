"""
Spares Management Router - PostgreSQL/Supabase Version
"""
from fastapi import APIRouter, HTTPException, Query, File, UploadFile
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from app.supabase_client import supabase
import logging
import json
import io
import polars as pl

logger = logging.getLogger(__name__)
router = APIRouter()

# Custom JSON encoder to handle date objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)

# Pydantic Models matching frontend
class SpareCreate(BaseModel):
    stock_code: str = Field(..., min_length=1, description="Unique stock code")
    description: str = Field(..., min_length=1, description="Part description")
    category: Optional[str] = Field(None, description="Primary category (legacy)")
    categories: Optional[List[str]] = Field(default_factory=list, description="Multi-category tags")
    machine_type: Optional[str] = Field(None, description="Machine type")
    current_quantity: int = Field(0, ge=0, description="Current stock quantity")
    min_quantity: int = Field(1, ge=0, description="Minimum stock level")
    max_quantity: int = Field(5, gt=0, description="Maximum stock level")
    unit_price: float = Field(0.0, ge=0, description="Unit price")
    unit_of_measure: Optional[str] = Field("UN", description="Unit of measure (UN, KG, EA, etc)")
    priority: str = Field("medium", description="Priority level")
    storage_location: Optional[str] = Field(None, description="Storage location")
    supplier: Optional[str] = Field(None, description="Supplier name")
    safety_stock: bool = Field(False, description="Safety stock flag")
    notes: Optional[str] = Field(None, description="Additional notes")
    lead_time_days: Optional[int] = Field(0, ge=0, description="Lead time in days")
    last_ordered_date: Optional[str] = Field(None, description="Last ordered date (ISO string)")

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

class SpareUpdate(BaseModel):
    stock_code: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = Field(None, min_length=1)
    category: Optional[str] = None
    categories: Optional[List[str]] = None
    machine_type: Optional[str] = None
    current_quantity: Optional[int] = Field(None, ge=0)
    min_quantity: Optional[int] = Field(None, ge=0)
    max_quantity: Optional[int] = Field(None, gt=0)
    unit_price: Optional[float] = Field(None, ge=0)
    unit_of_measure: Optional[str] = None
    priority: Optional[str] = None
    storage_location: Optional[str] = None
    supplier: Optional[str] = None
    safety_stock: Optional[bool] = None
    notes: Optional[str] = None
    lead_time_days: Optional[int] = Field(None, ge=0)
    last_ordered_date: Optional[str] = None

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

class BulkSpareCreate(BaseModel):
    items: List[SpareCreate]
    skip_existing: bool = Field(True, description="Skip items with existing stock codes")
    upsert: bool = Field(False, description="Update existing records instead of skipping them")

# Helper function to convert dates in records
def convert_dates_to_iso(record):
    """Convert date objects to ISO format strings for JSON serialization"""
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(value, (date, datetime)):
                record[key] = value.isoformat()
    return record

# Columns confirmed to exist in the Supabase spares table.
# Add more here once you run: ALTER TABLE spares ADD COLUMN IF NOT EXISTS ...
_DB_COLUMNS = {
    'stock_code', 'description', 'category', 'categories', 'machine_type',
    'current_quantity', 'min_quantity', 'max_quantity', 'unit_price',
    'priority', 'storage_location', 'supplier', 'safety_stock',
    'notes', 'unit_of_measure', 'lead_time_days', 'last_ordered_date',
}

# Helper function to clean data
def clean_data(data: dict) -> dict:
    """Clean data by removing None values and empty strings"""
    cleaned = {}
    for key, value in data.items():
        if value is not None and value != "":
            if isinstance(value, str):
                value = value.strip()
                if value:
                    cleaned[key] = value
            else:
                cleaned[key] = value
    return cleaned

def filter_for_db(data: dict) -> dict:
    """Strip fields that are not yet in the DB schema to avoid PGRST204 errors."""
    return {k: v for k, v in data.items() if k in _DB_COLUMNS}


# ─── COLUMN INFERENCE ────────────────────────────────────────────────────────

import re as _re

# ─── Category-row detection helpers ──────────────────────────────────────────

_CAT_PREFIX_RE = _re.compile(r'^(?:category|cat)\s*[:\-]\s*', _re.IGNORECASE)


def _is_category_row(values: tuple, is_bold: bool) -> bool:
    """Return True if this row is a category/group header, not a data row."""
    non_empty = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if not non_empty:
        return False
    first = non_empty[0]
    # Primary: "Category:" / "Cat:" prefix — always a header
    if _CAT_PREFIX_RE.match(first):
        return True
    # Secondary: bold row with no numeric values anywhere.
    # Data rows always have at least a price or qty (numeric); category headers are all text.
    if is_bold:
        def _is_numeric(s: str) -> bool:
            try:
                float(s.replace(',', '').replace('$', '').replace(' ', ''))
                return True
            except ValueError:
                return False
        if not any(_is_numeric(v) for v in non_empty):
            return True
    # Tertiary: exactly one non-numeric filled cell — standalone label even without bold
    if len(non_empty) == 1:
        try:
            float(first.replace(',', '').replace('$', '').replace(' ', ''))
            return False
        except ValueError:
            return True
    return False


def _extract_category_name(values: tuple) -> str:
    """Pull the category label out of a detected category row."""
    non_empty = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if not non_empty:
        return ''
    text = non_empty[0]
    m = _CAT_PREFIX_RE.match(text)
    return text[m.end():].strip() if m else text.strip()


_STOCK_HINTS  = {'code', 'stock', 'sku', 'no', 'number', 'ref', 'part', 'item code',
                 'stock code', 'part no', 'part number', 'item no', 'stockcode', 'partno',
                 'catalogue', 'catalog', 'part#', 'item#'}
_DESC_HINTS   = {'desc', 'description', 'name', 'item name', 'part name',
                 'item description', 'product', 'detail', 'material', 'long desc',
                 'full description', 'item description'}
_PRICE_HINTS  = {'price', 'cost', 'rate', 'unit price', 'unit cost', 'amount', 'amt',
                 'value', 'selling price', 'buy price', 'sell price', 'list price',
                 'retail', 'nett', 'net price', 'gross', 'tariff', 'total', 'sum',
                 'unit rate', 'each', 'per unit'}
# Columns that look like quantities — should never win the unit_price role
_QTY_HINTS    = {'qty', 'quantity', 'quant', 'count', 'balance', 'available',
                 'on hand', 'onhand', 'in stock', 'stk qty', 'stock qty',
                 'current qty', 'current stock', 'stock level', 'stock on hand'}


def _score_column(col: str, series: pl.Series) -> Dict[str, float]:
    """Return {'stock_code': s, 'description': s, 'unit_price': s} scores in [0, 1]."""
    scores = {'stock_code': 0.0, 'description': 0.0, 'unit_price': 0.0}
    col_lower = col.lower().strip()

    # ── Header hint (40% weight) ──────────────────────────────────────────────
    if any(h in col_lower for h in _STOCK_HINTS):
        scores['stock_code'] += 0.40
    if any(h in col_lower for h in _DESC_HINTS):
        scores['description'] += 0.40
    if any(h in col_lower for h in _PRICE_HINTS):
        scores['unit_price'] += 0.40
    # Hard penalty: quantity-named columns cannot be unit_price (e.g. "STK quantity")
    if any(h in col_lower for h in _QTY_HINTS):
        scores['unit_price'] = max(0.0, scores['unit_price'] - 0.70)

    # ── Data inference (60% weight) ───────────────────────────────────────────
    non_null = series.drop_nulls()
    if len(non_null) == 0:
        return scores

    is_numeric = series.dtype in (
        pl.Float32, pl.Float64,
        pl.Int8, pl.Int16, pl.Int32, pl.Int64,
        pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
    )
    numeric_vals: Optional[pl.Series] = None

    if is_numeric:
        numeric_vals = non_null.cast(pl.Float64, strict=False).drop_nulls()
    else:
        # Try to parse strings that look like numbers (handles "$1,250.00")
        cleaned = (
            non_null.cast(pl.Utf8, strict=False)
            .str.replace_all(r'[\$,€£\s]', '')
            .str.strip_chars()
        )
        parsed = cleaned.cast(pl.Float64, strict=False)
        n_parsed = parsed.drop_nulls().len()
        if n_parsed / max(len(non_null), 1) > 0.80:
            numeric_vals = parsed.drop_nulls()
            is_numeric = True

    if is_numeric and numeric_vals is not None and len(numeric_vals) > 0:
        non_neg   = float((numeric_vals >= 0).mean())
        sensible  = float(((numeric_vals > 0) & (numeric_vals < 1_000_000)).mean())
        # Decimal presence: prices usually have fractional parts; quantities rarely do.
        # Cast to Int64 and compare — if the truncated value != original → it has decimals.
        try:
            has_dec = float((numeric_vals != numeric_vals.cast(pl.Int64, strict=False).cast(pl.Float64)).mean())
        except Exception:
            has_dec = 0.0
        scores['unit_price'] += 0.60 * (non_neg * 0.20 + sensible * 0.40 + has_dec * 0.40)
    else:
        # String heuristics for stock_code / description
        str_vals = (
            non_null.cast(pl.Utf8, strict=False)
            .str.strip_chars()
        )
        str_vals = str_vals.filter(str_vals.str.len_chars() > 0)
        n = len(str_vals)
        if n == 0:
            return scores

        avg_len       = float(str_vals.str.len_chars().mean() or 0)
        unique_ratio  = str_vals.n_unique() / n
        has_spaces    = float(str_vals.str.contains(r' ').mean())
        no_spaces     = 1.0 - has_spaces
        # Alphanumeric + separators, 2–30 chars (typical stock code pattern)
        alnum_pattern = float(str_vals.str.contains(r'^[A-Za-z0-9][A-Za-z0-9\-_/\.]{1,29}$').mean())
        short_ratio   = max(0.0, 1.0 - max(0.0, avg_len - 20) / 30.0)

        stock_data = (
            unique_ratio  * 0.35 +
            short_ratio   * 0.30 +
            no_spaces     * 0.15 +
            alnum_pattern * 0.20
        )
        long_ratio = min(avg_len / 40.0, 1.0)
        desc_data  = long_ratio * 0.40 + has_spaces * 0.40 + unique_ratio * 0.20

        scores['stock_code']  += 0.60 * stock_data
        scores['description'] += 0.60 * desc_data

    return scores


def _infer_column_roles(df: pl.DataFrame) -> Dict[str, Any]:
    """
    Score every column then greedily assign unit_price → stock_code → description.
    Returns dict with keys: stock_code, description, unit_price and *_confidence.
    """
    all_scores = {col: _score_column(col, df[col]) for col in df.columns}
    result: Dict[str, Any] = {}
    used: set = set()

    for role in ('unit_price', 'stock_code', 'description'):
        candidates = [(col, all_scores[col][role]) for col in df.columns if col not in used]
        if not candidates:
            continue
        best_col, best_score = max(candidates, key=lambda x: x[1])
        result[role] = best_col
        result[f'{role}_confidence'] = round(min(best_score, 1.0), 2)
        used.add(best_col)

    return result


def _safe_val(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


@router.post("/infer")
async def infer_spare_columns(file: UploadFile = File(...)):
    """
    Upload an Excel (.xlsx) or CSV file.
    Polars reads it, inference engine scores every column,
    returns column mapping + all raw rows for client-side preview & re-mapping.
    """
    try:
        content = await file.read()
        fname = (file.filename or "").lower()

        if fname.endswith('.csv') or fname.endswith('.tsv'):
            sep = '\t' if fname.endswith('.tsv') else ','
            df = pl.read_csv(
                io.BytesIO(content),
                separator=sep,
                infer_schema_length=500,
                ignore_errors=True,
                null_values=['', 'NULL', 'null', 'N/A', 'n/a'],
            )
            # Ensure all column names are strings (CSV may produce numeric names)
            df = df.rename({c: str(c) for c in df.columns})
        else:
            # Load WITHOUT read_only so we can read cell.font.bold for category detection.
            import openpyxl as _oxl
            wb = _oxl.load_workbook(io.BytesIO(content), data_only=True)
            ws = wb.active

            # Collect (values_tuple, is_bold) for every row
            raw_row_data: List[tuple] = []
            for row_cells in ws.iter_rows():
                values  = tuple(cell.value for cell in row_cells)
                is_bold = any(
                    cell.font and cell.font.bold and cell.value is not None
                    for cell in row_cells
                )
                raw_row_data.append((values, is_bold))
            wb.close()

            if not raw_row_data:
                raise HTTPException(status_code=400, detail="File appears to be empty")

            # ── Build clean string headers from the first row ─────────────────
            raw_hdrs = list(raw_row_data[0][0])
            headers: List[str] = []
            seen: dict = {}
            for i, h in enumerate(raw_hdrs):
                name = str(h).strip() if h is not None else ""
                if not name or name.lower() == "none":
                    name = f"Column_{i + 1}"
                if name in seen:
                    seen[name] += 1
                    name = f"{name}_{seen[name]}"
                else:
                    seen[name] = 0
                headers.append(name)

            # ── Normalise a cell value to a Polars-safe scalar ────────────────
            def _norm(v):
                if v is None:           return None
                if isinstance(v, bool): return int(v)
                if isinstance(v, (int, float)): return v
                if hasattr(v, 'isoformat'): return v.isoformat()
                return str(v)

            # ── Walk data rows, detect category headers, track current cat ────
            n_cols          = len(headers)
            current_cat     = ''
            records         = []   # dicts for Polars (no _category)
            row_categories  = []   # parallel list — one entry per record

            for values, is_bold in raw_row_data[1:]:
                non_empty = [str(v).strip() for v in values if v is not None and str(v).strip()]
                if not non_empty:
                    continue  # fully blank row — skip

                if _is_category_row(values, is_bold):
                    current_cat = _extract_category_name(values)
                    continue  # category header — don't add to data records

                d = {headers[i]: _norm(values[i] if i < len(values) else None)
                     for i in range(n_cols)}
                records.append(d)
                row_categories.append(current_cat)

            if not records:
                raise HTTPException(status_code=400, detail="File contains no data rows")

            df = pl.DataFrame(records, infer_schema_length=len(records))

        if df.is_empty() or df.height == 0:
            raise HTTPException(status_code=400, detail="File contains no data rows")

        # Drop columns that are entirely null
        df = df[[c for c in df.columns if df[c].drop_nulls().len() > 0]]

        inferred = _infer_column_roles(df)

        # Build raw_rows — merge _category back in (it was kept out of the DataFrame
        # so it doesn't pollute the inference scoring).
        # `row_categories` only exists for xlsx paths; CSV paths have no category rows.
        cats: List[str] = locals().get('row_categories', [])
        raw_rows = []
        for idx, row in enumerate(df.to_dicts()):
            record: Dict[str, Any] = {k: _safe_val(v) for k, v in row.items()}
            cat = cats[idx] if idx < len(cats) else ''
            if cat:
                record['_category'] = cat
            raw_rows.append(record)

        has_categories = any(r.get('_category') for r in raw_rows)

        return {
            "inferred": {
                "stock_code":  inferred.get("stock_code"),
                "description": inferred.get("description"),
                "unit_price":  inferred.get("unit_price"),
            },
            "confidence": {
                "stock_code":  inferred.get("stock_code_confidence", 0),
                "description": inferred.get("description_confidence", 0),
                "unit_price":  inferred.get("unit_price_confidence", 0),
            },
            "all_columns": df.columns,
            "raw_rows": raw_rows,
            "total_rows": df.height,
            "has_categories": has_categories,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Column inference error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# GET all spares
@router.get("")
async def get_spares(
    search: Optional[str] = Query(None, description="Search in stock code, description, or notes"),
    category: Optional[str] = Query(None, description="Filter by category (checks both category and categories array)"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    limit: int = Query(100_000, ge=1, le=200_000, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Starting offset"),
):
    """Get all spares with optional filtering, paginating through Supabase's 1000-row cap."""
    try:
        def _build_query():
            q = supabase.table("spares").select("*")
            if search:
                q = q.or_(
                    f"stock_code.ilike.%{search}%,"
                    f"description.ilike.%{search}%,"
                    f"notes.ilike.%{search}%"
                )
            if category:
                q = q.or_(
                    f"category.eq.{category},"
                    f"categories.cs.{{\"{category}\"}}"
                )
            if priority:
                q = q.eq("priority", priority)
            return q.order("stock_code", desc=False)

        # Supabase PostgREST caps each request at 1000 rows.
        # Loop with .range() until we have everything.
        PAGE          = 1000
        all_records: List[Dict[str, Any]] = []
        current_start = offset

        while len(all_records) < limit:
            fetch_n = min(PAGE, limit - len(all_records))
            res   = _build_query().range(current_start, current_start + fetch_n - 1).execute()
            batch = res.data or []
            all_records.extend(batch)
            if len(batch) < fetch_n:
                break                   # last page — no more rows
            current_start += fetch_n

        for record in all_records:
            convert_dates_to_iso(record)
            if 'categories' not in record or record['categories'] is None:
                record['categories'] = []

        return all_records

    except Exception as e:
        logger.error(f"Error fetching spares: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching spares: {str(e)}")

# GET single spare
@router.get("/{spare_id}")
async def get_spare(spare_id: int):
    """Get a specific spare by ID"""
    try:
        response = supabase.table("spares").select("*").eq("id", spare_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Spare part not found")
        
        result = response.data[0]
        convert_dates_to_iso(result)
        if 'categories' not in result or result['categories'] is None:
            result['categories'] = []
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching spare: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching spare: {str(e)}")

# POST bulk create spares
@router.post("/bulk", status_code=201)
async def bulk_create_spares(payload: BulkSpareCreate):
    """
    Bulk-insert spare parts efficiently:
    - One batched IN() query to find already-existing stock codes (no N+1)
    - Batch inserts of 100 rows per Supabase call instead of one-at-a-time
    This keeps 4 000+ row imports well within timeout limits.
    """
    try:
        items = payload.items
        if not items:
            return {"created": 0, "skipped": 0, "errors": 0, "total": 0}

        BATCH = 100
        created = 0
        skipped = 0
        updated = 0
        errors  = 0

        if payload.upsert:
            # ── UPSERT: manual fallback — no UNIQUE constraint required ──────────
            # Step 1: batched IN() to find which codes already exist
            CHECK_BATCH  = 200
            all_codes    = [item.stock_code for item in items]
            existing_codes: set = set()
            for i in range(0, len(all_codes), CHECK_BATCH):
                res = supabase.table("spares").select("stock_code") \
                              .in_("stock_code", all_codes[i:i + CHECK_BATCH]).execute()
                existing_codes.update(r["stock_code"] for r in (res.data or []))

            new_items = [item for item in items if item.stock_code not in existing_codes]
            upd_items = [item for item in items if item.stock_code in existing_codes]

            # Step 2: batch-insert brand-new rows
            for i in range(0, len(new_items), BATCH):
                batch = new_items[i:i + BATCH]
                try:
                    supabase.table("spares").insert(
                        [filter_for_db(item.dict()) for item in batch]
                    ).execute()
                    created += len(batch)
                except Exception as ins_err:
                    logger.warning(f"Insert batch {i // BATCH} failed, going row-by-row: {ins_err}")
                    for item in batch:
                        try:
                            supabase.table("spares").insert(filter_for_db(item.dict())).execute()
                            created += 1
                        except Exception as row_err:
                            logger.warning(f"Insert row error {item.stock_code}: {row_err}")
                            errors += 1

            # Step 3: update existing rows one at a time (update by stock_code needs no UNIQUE index)
            for item in upd_items:
                try:
                    upd_data = {k: v for k, v in filter_for_db(item.dict()).items()
                                if k != 'stock_code'}
                    supabase.table("spares").update(upd_data).eq("stock_code", item.stock_code).execute()
                    updated += 1
                except Exception as upd_err:
                    logger.warning(f"Update error {item.stock_code}: {upd_err}")
                    errors += 1

        else:
            # ── SKIP existing: one batched IN() check, then insert only new rows ─────
            existing_codes: set = set()
            if payload.skip_existing:
                CHECK_BATCH = 200
                all_codes   = [item.stock_code for item in items]
                for i in range(0, len(all_codes), CHECK_BATCH):
                    res = supabase.table("spares").select("stock_code") \
                                  .in_("stock_code", all_codes[i:i + CHECK_BATCH]).execute()
                    existing_codes.update(r["stock_code"] for r in (res.data or []))

            new_items = [item for item in items if item.stock_code not in existing_codes]
            skipped   = len(items) - len(new_items)

            for i in range(0, len(new_items), BATCH):
                batch   = new_items[i:i + BATCH]
                records = [filter_for_db(item.dict()) for item in batch]
                try:
                    supabase.table("spares").insert(records).execute()
                    created += len(batch)
                except Exception as batch_err:
                    logger.warning(f"Insert batch error (rows {i}–{i + len(batch)}): {batch_err}")
                    for item in batch:
                        try:
                            supabase.table("spares").insert(filter_for_db(item.dict())).execute()
                            created += 1
                        except Exception as row_err:
                            logger.warning(f"Row error {item.stock_code}: {row_err}")
                            errors += 1

        logger.info(f"Bulk import: {created} inserted, {updated} updated, {skipped} skipped, {errors} errors / {len(items)} total")
        return {"created": created, "updated": updated, "skipped": skipped, "errors": errors, "total": len(items)}

    except Exception as e:
        logger.error(f"Bulk create error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Bulk create failed: {str(e)}")


# POST create spare
@router.post("", status_code=201)
async def create_spare(spare: SpareCreate):
    """Create a new spare part"""
    try:
        # Check if stock code already exists
        existing = supabase.table("spares").select("id").eq("stock_code", spare.stock_code).execute()
        
        if existing.data:
            raise HTTPException(status_code=400, detail=f"Stock code '{spare.stock_code}' already exists")
        
        # Insert new spare (filter to known DB columns only)
        response = supabase.table("spares").insert(filter_for_db(spare.dict())).execute()
        
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to create spare part")
        
        logger.info(f"Created spare part: {spare.stock_code}")
        
        result = response.data[0]
        convert_dates_to_iso(result)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating spare: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating spare: {str(e)}")

# PUT update spare
@router.put("/{spare_id}")
async def update_spare(spare_id: int, spare_update: SpareUpdate):
    """Update an existing spare part"""
    try:
        # Check if spare exists
        existing = supabase.table("spares").select("*").eq("id", spare_id).execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Spare part not found")
        
        # Check stock code conflict if updating
        if spare_update.stock_code:
            conflict = supabase.table("spares") \
                .select("id") \
                .eq("stock_code", spare_update.stock_code) \
                .neq("id", spare_id) \
                .execute()
            
            if conflict.data:
                raise HTTPException(status_code=400, detail=f"Stock code '{spare_update.stock_code}' already exists")
        
        # Clean and filter update data to known DB columns only
        update_data = filter_for_db(clean_data(spare_update.dict(exclude_unset=True)))
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No data provided for update")
        
        # Update in database
        response = supabase.table("spares").update(update_data).eq("id", spare_id).execute()

        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to update spare part")

        logger.info(f"Updated spare part: {spare_id}")

        # Propagate price change into stock_issues JSONB items so historical
        # records reflect the current catalogue price.
        # Requires this function in Supabase (run once):
        #
        #   CREATE OR REPLACE FUNCTION sync_issue_item_prices(p_stock_code TEXT, p_new_price FLOAT8)
        #   RETURNS INTEGER AS $$
        #   DECLARE updated_count INTEGER := 0;
        #   BEGIN
        #     UPDATE stock_issues
        #     SET items = (
        #       SELECT jsonb_agg(
        #         CASE WHEN item->>'stock_code' = p_stock_code
        #              THEN jsonb_set(item, '{unit_price}', to_jsonb(p_new_price))
        #              ELSE item END
        #       )
        #       FROM jsonb_array_elements(items) AS item
        #     )
        #     WHERE items @> jsonb_build_array(jsonb_build_object('stock_code', p_stock_code));
        #     GET DIAGNOSTICS updated_count = ROW_COUNT;
        #     RETURN updated_count;
        #   END;
        #   $$ LANGUAGE plpgsql SECURITY DEFINER;
        if 'unit_price' in update_data:
            old_stock_code = existing.data[0].get('stock_code')
            try:
                sync_result = supabase.rpc('sync_issue_item_prices', {
                    'p_stock_code': old_stock_code,
                    'p_new_price': float(update_data['unit_price']),
                }).execute()
                updated_issues = sync_result.data or 0
                if updated_issues:
                    logger.info(f"Synced price ${update_data['unit_price']} for {old_stock_code} into {updated_issues} issue record(s)")
            except Exception as sync_err:
                # Non-fatal — function may not exist yet (migration pending)
                logger.warning(f"Price sync to stock_issues skipped: {sync_err}")

        result = response.data[0]
        convert_dates_to_iso(result)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating spare: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating spare: {str(e)}")

# DELETE spare
@router.delete("/{spare_id}")
async def delete_spare(spare_id: int):
    """Delete a spare part"""
    try:
        # Check if spare exists
        existing = supabase.table("spares").select("*").eq("id", spare_id).execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Spare part not found")
        
        spare_data = existing.data[0]
        
        # Delete from database
        supabase.table("spares").delete().eq("id", spare_id).execute()
        
        logger.info(f"Deleted spare part: {spare_id} - {spare_data.get('stock_code', 'Unknown')}")
        
        return {
            "message": "Spare part deleted successfully",
            "id": spare_id,
            "stock_code": spare_data.get('stock_code', 'Unknown')
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting spare: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting spare: {str(e)}")

# GET suggestions for a field
@router.get("/suggestions/{field}")
async def get_suggestions(field: str):
    """Get unique values for a field for auto-suggestions"""
    allowed_fields = ['category', 'machine_type', 'priority', 'storage_location', 'supplier']
    
    if field not in allowed_fields:
        raise HTTPException(status_code=400, detail=f"Field '{field}' not allowed for suggestions")
    
    try:
        # Get distinct values
        response = supabase.table("spares").select(field).execute()
        
        if not response.data:
            return {"suggestions": []}
        
        # Extract unique non-empty values
        values = set()
        for item in response.data:
            if field in item and item[field] and str(item[field]).strip():
                values.add(str(item[field]).strip())
        
        suggestions = list(sorted(values))
        return {"suggestions": suggestions}
        
    except Exception as e:
        logger.error(f"Error getting suggestions for {field}: {e}")
        return {"suggestions": []}

# GET statistics
@router.get("/stats/summary")
async def get_stats():
    """Get summary statistics"""
    try:
        # Get all spares
        response = supabase.table("spares").select("*").execute()
        
        if not response.data:
            return {
                "total": 0,
                "out_of_stock": 0,
                "low_stock": 0,
                "critical": 0,
                "safety_stock": 0,
                "total_value": 0
            }
        
        spares = response.data
        total = len(spares)
        
        out_of_stock = 0
        low_stock = 0
        critical = 0
        safety_stock = 0
        total_value = 0
        
        for spare in spares:
            current = spare.get('current_quantity', 0) or 0
            min_qty = spare.get('min_quantity', 1) or 1
            price = spare.get('unit_price', 0) or 0
            
            # Calculate inventory value
            total_value += current * price
            
            # Count statuses
            if current <= 0:
                out_of_stock += 1
            elif current <= min_qty:
                low_stock += 1
            
            # Count critical items
            if spare.get('priority') == 'critical':
                critical += 1
            
            # Count safety stock items
            if spare.get('safety_stock') == True:
                safety_stock += 1
        
        return {
            "total": total,
            "out_of_stock": out_of_stock,
            "low_stock": low_stock,
            "critical": critical,
            "safety_stock": safety_stock,
            "total_value": round(total_value, 2)
        }
        
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}")
        return {
            "total": 0,
            "out_of_stock": 0,
            "low_stock": 0,
            "critical": 0,
            "safety_stock": 0,
            "total_value": 0
        }

# Health check endpoint
@router.get("/health/check")
async def spares_health_check():
    """Health check endpoint for spares router"""
    try:
        response = supabase.table("spares").select("id", count="exact").limit(1).execute()
        
        return {
            "status": "healthy", 
            "service": "spares", 
            "database": "connected",
            "timestamp": datetime.utcnow().isoformat(),
            "table_exists": True
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

# Test endpoint
@router.get("/test/connection")
async def test_connection():
    """Test database connection"""
    try:
        response = supabase.table("spares").select("id", count="exact").limit(1).execute()
        
        return {
            "status": "ok",
            "database": "connected",
            "table": "spares",
            "record_count": len(response.data) if response.data else 0
        }
    except Exception as e:
        return {"status": "error", "database": "disconnected", "error": str(e)}

# Export data endpoint
@router.get("/export/data")
async def export_spares():
    """Export all spares as JSON"""
    try:
        response = supabase.table("spares").select("*").order("stock_code").execute()

        spares = response.data or []
        for record in spares:
            convert_dates_to_iso(record)

        return {
            "export_date": datetime.now().isoformat(),
            "count": len(spares),
            "spares": spares
        }

    except Exception as e:
        logger.error(f"Error exporting spares: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error exporting spares: {str(e)}")

# ── Saved Spare Requisitions ──────────────────────────────────────────────────
# Run this SQL in Supabase before using these endpoints:
#
#   CREATE TABLE spare_requisitions (
#     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
#     name TEXT NOT NULL,
#     saved_at TIMESTAMPTZ DEFAULT NOW(),
#     updated_at TIMESTAMPTZ DEFAULT NOW(),
#     requester TEXT,
#     reason TEXT,
#     urgency TEXT DEFAULT 'routine',
#     priority TEXT DEFAULT 'medium',
#     required_for TEXT,
#     lines JSONB DEFAULT '[]',
#     grand_total NUMERIC DEFAULT 0
#   );

class SavedSpareReqCreate(BaseModel):
    name: str = Field(..., min_length=1)
    requester: Optional[str] = None
    reason: Optional[str] = None
    urgency: str = 'routine'
    priority: str = 'medium'
    required_for: Optional[str] = None
    lines: List[dict] = Field(default_factory=list)
    grand_total: float = 0.0

@router.get("/saved-requisitions")
async def get_saved_spare_requisitions():
    """Fetch all saved spare requisitions from Supabase"""
    try:
        response = supabase.table("spare_requisitions").select("*").order("updated_at", desc=True).execute()
        return response.data or []
    except Exception as e:
        logger.warning(f"spare_requisitions table may not exist yet: {e}")
        return []

@router.post("/saved-requisitions", status_code=201)
async def create_saved_spare_requisition(data: SavedSpareReqCreate):
    """Save a spare requisition to Supabase"""
    try:
        now = datetime.utcnow().isoformat()
        row = {**data.dict(), "saved_at": now, "updated_at": now}
        response = supabase.table("spare_requisitions").insert(row).execute()
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to save requisition")
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving spare requisition: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/saved-requisitions/{req_id}")
async def update_saved_spare_requisition(req_id: str, data: SavedSpareReqCreate):
    """Update a saved spare requisition"""
    try:
        now = datetime.utcnow().isoformat()
        row = {**data.dict(), "updated_at": now}
        response = supabase.table("spare_requisitions").update(row).eq("id", req_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Saved requisition not found")
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating spare requisition: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/saved-requisitions/{req_id}")
async def delete_saved_spare_requisition(req_id: str):
    """Delete a saved spare requisition"""
    try:
        supabase.table("spare_requisitions").delete().eq("id", req_id).execute()
        return {"message": "Deleted", "id": req_id}
    except Exception as e:
        logger.error(f"Error deleting spare requisition: {e}")
        raise HTTPException(status_code=500, detail=str(e))