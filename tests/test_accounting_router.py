# tests/test_accounting_router.py — the Accounting & Financial module.
#
# Three things get tested that no existing test covers:
#  1. Receipt-number generation + unique-violation retry (mirrors
#     test_work_order_number.py's proven pattern for the identical shape).
#  2. The /summary, /trend, and /breakdown aggregate math.
#  3. The whole-router manager+ gate actually rejects an insufficient-role
#     caller (403), not just an anonymous one (401, already covered by
#     test_endpoint_auth.py) — this is the first whole-router role gate in
#     the codebase, so it's new coverage, not a copy of an existing pattern.
#
# The three CrudRouter-backed sub-resources (expenses/assets/liabilities)
# don't get their own CRUD-shape tests here — that generic behavior (list/
# create/update/delete semantics) is already exhaustively covered by
# test_crud_router.py against the shared base class itself.

import pytest
import app.routers.accounting as acc


# ─── Fakes ──────────────────────────────────────────────────────────────────

class _SelectResp:
    def __init__(self, rows):
        self.data = rows


class _FakeTable:
    """Returns canned rows for the given table name on select().execute();
    on insert().execute() raises a unique-violation the first `fail_times`
    calls, then succeeds. gte/lte/eq/order/or_ are harmless no-ops so
    apply_date_range() and the hand-written filters chain without error."""
    def __init__(self, name, state):
        self.name = name
        self.state = state

    def select(self, *a, **k):
        self.state["mode"] = "select"
        return self

    def insert(self, data):
        self.state["mode"] = "insert"
        self.state["last_insert"] = data
        return self

    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def neq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def or_(self, *a, **k): return self

    def execute(self):
        if self.state["mode"] == "select":
            return _SelectResp(self.state["tables"].get(self.name, []))
        self.state["attempts"] += 1
        if self.state["attempts"] <= self.state["fail_times"]:
            raise Exception('duplicate key value violates unique constraint "uq_accounting_transactions_receipt_number"')
        return _SelectResp([dict(self.state["last_insert"], id=1)])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeTable(name, self.state)


def _patch(monkeypatch, tables=None, fail_times=0):
    state = {"tables": tables or {}, "attempts": 0, "fail_times": fail_times, "mode": None, "last_insert": None}
    monkeypatch.setattr(acc, "supabase", _FakeSupabase(state))
    return state


# ─── _generate_receipt_number ──────────────────────────────────────────────

def test_generate_uses_max_trailing_digits(monkeypatch):
    _patch(monkeypatch, {"accounting_transactions": [
        {"receipt_number": "RCPT-00099"}, {"receipt_number": "RCPT-00123"}, {"receipt_number": None},
    ]})
    assert acc._generate_receipt_number() == "RCPT-00124"
    assert acc._generate_receipt_number(offset=2) == "RCPT-00126"


def test_generate_from_empty_table(monkeypatch):
    _patch(monkeypatch, {"accounting_transactions": []})
    assert acc._generate_receipt_number() == "RCPT-00001"


@pytest.mark.parametrize("msg", [
    'duplicate key value violates unique constraint "uq_accounting_transactions_receipt_number"',
    "postgrest error code 23505",
    "UNIQUE constraint failed",
])
def test_detects_unique_violation(msg):
    assert acc._is_unique_violation(Exception(msg)) is True


def test_ignores_other_errors():
    assert acc._is_unique_violation(Exception("connection reset")) is False


# ─── create_transaction — end-to-end retry behavior ────────────────────────

@pytest.mark.asyncio
async def test_create_transaction_retries_on_conflict(monkeypatch):
    state = _patch(monkeypatch, {"accounting_transactions": []}, fail_times=2)
    data = acc.TransactionCreate(transaction_date="2026-07-30", service_type="Website", amount=250.0)
    result = await acc.create_transaction(data, current_user={"role": "manager"})
    assert state["attempts"] == 3  # 2 failures + 1 success
    # Each retry regenerates the number with a bumped offset — RCPT-00001 and
    # RCPT-00002 are the ones that collided, RCPT-00003 is the one that stuck.
    assert result["receipt_number"] == "RCPT-00003"


@pytest.mark.asyncio
async def test_create_transaction_gives_up_after_6_attempts(monkeypatch):
    _patch(monkeypatch, {"accounting_transactions": []}, fail_times=99)
    from fastapi import HTTPException
    data = acc.TransactionCreate(transaction_date="2026-07-30", service_type="CV", amount=20.0)
    with pytest.raises(HTTPException) as exc:
        await acc.create_transaction(data, current_user={"role": "manager"})
    assert exc.value.status_code == 409


# ─── /summary ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_computes_profit_or_loss(monkeypatch):
    _patch(monkeypatch, {
        "accounting_transactions": [{"amount": 300}, {"amount": 200}],
        "accounting_expenses": [{"amount": 150}],
        "accounting_assets": [{"value": 1000}, {"value": 500}],
        "accounting_liabilities": [{"amount": 400}],
    })
    result = await acc.get_summary()
    assert result["revenue"] == 500
    assert result["expenses"] == 150
    assert result["profit_or_loss"] == 350
    assert result["assets_total"] == 1500
    assert result["liabilities_total"] == 400
    assert result["net_worth"] == 1100
    assert result["transaction_count"] == 2
    assert result["expense_count"] == 1


@pytest.mark.asyncio
async def test_summary_empty_tables_are_all_zero(monkeypatch):
    _patch(monkeypatch, {})
    result = await acc.get_summary()
    assert result == {
        "revenue": 0, "expenses": 0, "profit_or_loss": 0,
        "transaction_count": 0, "expense_count": 0,
        "assets_total": 0, "liabilities_total": 0, "net_worth": 0,
    }


# ─── /trend ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trend_buckets_by_month(monkeypatch):
    from datetime import date
    today = date.today()
    this_month = f"{today.year:04d}-{today.month:02d}"
    _patch(monkeypatch, {
        "accounting_transactions": [{"transaction_date": f"{this_month}-05", "amount": 100}],
        "accounting_expenses": [{"expense_date": f"{this_month}-10", "amount": 40}],
    })
    result = await acc.get_trend(months=3)
    assert len(result) == 3
    current = next(r for r in result if r["month"] == this_month)
    assert current["revenue"] == 100
    assert current["expenses"] == 40
    assert current["profit"] == 60


# ─── /breakdown ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expenses_by_category_grouped_and_sorted(monkeypatch):
    _patch(monkeypatch, {"accounting_expenses": [
        {"category": "Equipment", "amount": 100},
        {"category": "Subscriptions", "amount": 300},
        {"category": "Equipment", "amount": 50},
    ]})
    result = await acc.get_expenses_by_category()
    assert result[0] == {"category": "Subscriptions", "total": 300}
    assert result[1] == {"category": "Equipment", "total": 150}


@pytest.mark.asyncio
async def test_revenue_by_service_grouped_and_sorted(monkeypatch):
    _patch(monkeypatch, {"accounting_transactions": [
        {"service_type": "CV", "amount": 20},
        {"service_type": "Website", "amount": 500},
    ]})
    result = await acc.get_revenue_by_service()
    assert result[0] == {"service_type": "Website", "total": 500}


# ─── Whole-router manager+ gate (403 for insufficient role) ────────────────
# The router-level dependencies=[Depends(require_role('manager'))] gate
# (wired in main.py) is new: no other router in this codebase is gated as a
# whole rather than per-route. This boots the real app to prove it actually
# rejects a signed-in-but-insufficient-role caller, not just an anonymous one.

class _FakeAuthClient:
    def __init__(self, user):
        self._user = user

    def get_user(self, token):
        class R: pass
        r = R()
        r.user = self._user
        return r


class _FakeRpcResult:
    def __init__(self, data):
        self.data = data


class _FakeAuthSupabase:
    def __init__(self, user, role):
        self.auth = _FakeAuthClient(user)
        self._role = role

    def rpc(self, name, params):
        return self

    def execute(self):
        return _FakeRpcResult(self._role)


class _FakeAuthUser:
    def __init__(self, uid="u-1", email="a@b.com"):
        self.id = uid
        self.email = email


def test_insufficient_role_is_403_not_401(monkeypatch):
    from fastapi.testclient import TestClient
    from app import auth as auth_mod
    from main import app

    monkeypatch.setattr(auth_mod, "supabase", _FakeAuthSupabase(_FakeAuthUser(), role="user"))
    client = TestClient(app)
    resp = client.get("/api/accounting/transactions", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 403, f"expected 403 for insufficient role, got {resp.status_code}: {resp.text}"


def test_manager_role_passes_the_gate(monkeypatch):
    from fastapi.testclient import TestClient
    from app import auth as auth_mod
    from main import app

    monkeypatch.setattr(auth_mod, "supabase", _FakeAuthSupabase(_FakeAuthUser(), role="manager"))
    monkeypatch.setattr(acc, "supabase", _FakeSupabase({"tables": {"accounting_transactions": []}, "attempts": 0, "fail_times": 0, "mode": None, "last_insert": None}))
    client = TestClient(app)
    resp = client.get("/api/accounting/transactions", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 200, f"expected 200 for manager role, got {resp.status_code}: {resp.text}"
