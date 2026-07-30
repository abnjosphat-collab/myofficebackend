# app/routers/accounting.py — Finance & Accounting module: sales transactions
# (with a server-generated sequential receipt number), expenses, and a simple
# assets/liabilities register for a profit/loss + net-worth summary.
#
# The whole router is manager+-gated at mount time in main.py
# (`register_router("accounting", ..., dependencies=[Depends(require_role('manager'))])`)
# — every route below still carries its own get_current_user/require_role too,
# which is now redundant but harmless, and protects against the router ever
# being re-mounted elsewhere without the outer gate.
import re
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from app.crud_router import CrudRouter
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.db_helpers import get_or_404, apply_date_range, distinct_suggestions

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Transactions (sales) — hand-written: needs a server-generated receipt
# number on create, which CrudRouter has no hook for ──────────────────────────

class TransactionCreate(BaseModel):
    transaction_date: str
    service_type: str
    description: Optional[str] = None
    client_name: Optional[str] = None
    amount: float = Field(..., gt=0)
    notes: Optional[str] = None


class TransactionUpdate(BaseModel):
    transaction_date: Optional[str] = None
    service_type: Optional[str] = None
    description: Optional[str] = None
    client_name: Optional[str] = None
    amount: Optional[float] = Field(None, gt=0)
    notes: Optional[str] = None


def _generate_receipt_number(offset: int = 0) -> str:
    """Next receipt number, server-side: RCPT-<max trailing digits + 1 + offset>,
    5-wide. Mirrors maintenance.py's _generate_wo_number — same proven shape."""
    resp = supabase.table("accounting_transactions").select("receipt_number").execute()
    max_n = 0
    for row in (resp.data or []):
        m = re.search(r'(\d+)$', row.get("receipt_number") or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"RCPT-{str(max_n + 1 + offset).zfill(5)}"


def _is_unique_violation(err: Exception) -> bool:
    s = str(err).lower()
    return '23505' in s or 'duplicate key' in s or 'uq_accounting_transactions_receipt_number' in s or 'unique constraint' in s


@router.get("/transactions", dependencies=[Depends(get_current_user)])
async def list_transactions(date_from: Optional[str] = None, date_to: Optional[str] = None,
                             service_type: Optional[str] = None, search: Optional[str] = None):
    q = supabase.table("accounting_transactions").select("*")
    q = apply_date_range(q, "transaction_date", date_from, date_to)
    if service_type:
        q = q.eq("service_type", service_type)
    if search:
        q = q.or_(f"client_name.ilike.%{search}%,description.ilike.%{search}%,receipt_number.ilike.%{search}%")
    q = q.order("transaction_date", desc=True)
    return (q.execute()).data or []


@router.get("/transactions/{tx_id}", dependencies=[Depends(get_current_user)])
async def get_transaction(tx_id: int):
    return get_or_404(supabase, "accounting_transactions", tx_id, detail="Transaction not found")


@router.post("/transactions")
async def create_transaction(data: TransactionCreate, current_user: dict = Depends(get_current_user)):
    payload = data.dict(exclude_none=True)
    for attempt in range(6):
        payload["receipt_number"] = _generate_receipt_number(offset=attempt)
        try:
            r = supabase.table("accounting_transactions").insert(payload).execute()
        except Exception as e:
            if _is_unique_violation(e):
                if attempt < 5:
                    logger.warning(f"Receipt number {payload['receipt_number']} taken, retrying: {e}")
                    continue
                # Exhausted all 6 attempts on genuine collisions — a "please retry"
                # 409 is more honest here than surfacing the raw DB error as a 500.
                raise HTTPException(status_code=409, detail="Could not allocate a unique receipt number — please retry.")
            raise HTTPException(status_code=500, detail=str(e))
        if r.data:
            return r.data[0]
        raise HTTPException(status_code=500, detail="Insert failed")


@router.patch("/transactions/{tx_id}")
async def update_transaction(tx_id: int, data: TransactionUpdate, current_user: dict = Depends(get_current_user)):
    payload = data.dict(exclude_unset=True)
    r = supabase.table("accounting_transactions").update(payload).eq("id", tx_id).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return r.data[0]


@router.delete("/transactions/{tx_id}")
async def delete_transaction(tx_id: int, current_user: dict = Depends(require_role('manager'))):
    supabase.table("accounting_transactions").delete().eq("id", tx_id).execute()
    return {"ok": True}


# ─── Expenses / Assets / Liabilities — textbook CrudRouter, no create-time
# side effects ──────────────────────────────────────────────────────────────

class ExpenseCreate(BaseModel):
    expense_date: str
    category: str
    vendor: Optional[str] = None
    description: Optional[str] = None
    amount: float = Field(..., gt=0)
    payment_method: Optional[str] = None
    notes: Optional[str] = None


class ExpenseUpdate(BaseModel):
    expense_date: Optional[str] = None
    category: Optional[str] = None
    vendor: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[float] = Field(None, gt=0)
    payment_method: Optional[str] = None
    notes: Optional[str] = None


class AssetCreate(BaseModel):
    name: str
    category: str
    acquired_date: Optional[str] = None
    value: float = Field(0.0, ge=0.0)
    notes: Optional[str] = None


class AssetUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    acquired_date: Optional[str] = None
    value: Optional[float] = Field(None, ge=0.0)
    notes: Optional[str] = None


class LiabilityCreate(BaseModel):
    name: str
    category: str
    due_date: Optional[str] = None
    amount: float = Field(0.0, ge=0.0)
    notes: Optional[str] = None


class LiabilityUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    due_date: Optional[str] = None
    amount: Optional[float] = Field(None, ge=0.0)
    notes: Optional[str] = None


expenses_crud = CrudRouter(
    "accounting_expenses", ExpenseCreate, ExpenseUpdate,
    tags=["Accounting"], order_by="expense_date", order_desc=True,
    filters={"category": "category"},
    search_columns=["category", "vendor", "description"],
    not_found="Expense not found",
).router
router.include_router(expenses_crud, prefix="/expenses")

assets_crud = CrudRouter(
    "accounting_assets", AssetCreate, AssetUpdate,
    tags=["Accounting"], order_by="name",
    filters={"category": "category"},
    search_columns=["name", "category"],
    not_found="Asset not found",
).router
router.include_router(assets_crud, prefix="/assets")

liabilities_crud = CrudRouter(
    "accounting_liabilities", LiabilityCreate, LiabilityUpdate,
    tags=["Accounting"], order_by="name",
    filters={"category": "category"},
    search_columns=["name", "category"],
    not_found="Liability not found",
).router
router.include_router(liabilities_crud, prefix="/liabilities")


# ─── Aggregates ───────────────────────────────────────────────────────────────

@router.get("/summary", dependencies=[Depends(get_current_user)])
async def get_summary(date_from: Optional[str] = None, date_to: Optional[str] = None):
    tx_q = apply_date_range(supabase.table("accounting_transactions").select("amount"),
                             "transaction_date", date_from, date_to)
    exp_q = apply_date_range(supabase.table("accounting_expenses").select("amount"),
                              "expense_date", date_from, date_to)
    tx_rows = (tx_q.execute()).data or []
    exp_rows = (exp_q.execute()).data or []
    revenue = sum(r.get("amount") or 0 for r in tx_rows)
    expenses = sum(r.get("amount") or 0 for r in exp_rows)
    assets_total = sum(r.get("value") or 0 for r in (supabase.table("accounting_assets").select("value").execute()).data or [])
    liabilities_total = sum(r.get("amount") or 0 for r in (supabase.table("accounting_liabilities").select("amount").execute()).data or [])
    return {
        "revenue": revenue,
        "expenses": expenses,
        "profit_or_loss": revenue - expenses,
        "transaction_count": len(tx_rows),
        "expense_count": len(exp_rows),
        "assets_total": assets_total,
        "liabilities_total": liabilities_total,
        "net_worth": assets_total - liabilities_total,
    }


@router.get("/trend", dependencies=[Depends(get_current_user)])
async def get_trend(months: int = 12):
    """Monthly revenue/expenses/profit for the last `months` months (oldest first)."""
    tx_rows = (supabase.table("accounting_transactions").select("transaction_date,amount").execute()).data or []
    exp_rows = (supabase.table("accounting_expenses").select("expense_date,amount").execute()).data or []

    from datetime import date as _date
    today = _date.today()
    # Build the ordered list of "YYYY-MM" keys for the trailing `months` months.
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    keys.reverse()

    revenue_by_month = {k: 0.0 for k in keys}
    expenses_by_month = {k: 0.0 for k in keys}
    for r in tx_rows:
        key = (r.get("transaction_date") or "")[:7]
        if key in revenue_by_month:
            revenue_by_month[key] += r.get("amount") or 0
    for r in exp_rows:
        key = (r.get("expense_date") or "")[:7]
        if key in expenses_by_month:
            expenses_by_month[key] += r.get("amount") or 0

    return [
        {"month": k, "revenue": revenue_by_month[k], "expenses": expenses_by_month[k],
         "profit": revenue_by_month[k] - expenses_by_month[k]}
        for k in keys
    ]


@router.get("/breakdown/expenses-by-category", dependencies=[Depends(get_current_user)])
async def get_expenses_by_category(date_from: Optional[str] = None, date_to: Optional[str] = None):
    q = apply_date_range(supabase.table("accounting_expenses").select("category,amount"),
                          "expense_date", date_from, date_to)
    rows = (q.execute()).data or []
    totals: dict = {}
    for r in rows:
        cat = r.get("category") or "Other"
        totals[cat] = totals.get(cat, 0) + (r.get("amount") or 0)
    return sorted(({"category": k, "total": v} for k, v in totals.items()), key=lambda x: -x["total"])


@router.get("/breakdown/revenue-by-service", dependencies=[Depends(get_current_user)])
async def get_revenue_by_service(date_from: Optional[str] = None, date_to: Optional[str] = None):
    q = apply_date_range(supabase.table("accounting_transactions").select("service_type,amount"),
                          "transaction_date", date_from, date_to)
    rows = (q.execute()).data or []
    totals: dict = {}
    for r in rows:
        svc = r.get("service_type") or "Other"
        totals[svc] = totals.get(svc, 0) + (r.get("amount") or 0)
    return sorted(({"service_type": k, "total": v} for k, v in totals.items()), key=lambda x: -x["total"])


# ─── Suggestions (autocomplete) ────────────────────────────────────────────────

@router.get("/suggestions/service-types", dependencies=[Depends(get_current_user)])
async def suggest_service_types(search: Optional[str] = None):
    return await distinct_suggestions(supabase, "accounting_transactions", "service_type", search, "service type")


@router.get("/suggestions/expense-categories", dependencies=[Depends(get_current_user)])
async def suggest_expense_categories(search: Optional[str] = None):
    return await distinct_suggestions(supabase, "accounting_expenses", "category", search, "expense category")
