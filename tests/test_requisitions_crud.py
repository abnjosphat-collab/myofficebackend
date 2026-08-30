# tests/test_requisitions_crud.py — create_requisition, get_requisitions,
# get_requisition, delete_requisition, and health_check had zero prior tests (only the
# PATCH-null-clearing and cost/stats endpoints were covered). Uses a per-table
# response-queue fake, generalized from test_documents_folder_rename.py's
# call-recording recipe: each table name gets its own ordered queue of canned
# responses, since these handlers touch "requisitions" and "requisition_items" in a
# fixed, known sequence per call.
#
# NOTE: get_requisitions accepts a `search` query parameter that is never actually
# applied to the query (no .ilike/.or_ call references it anywhere in the function) —
# not the "fake 200 on failure" anti-pattern the task flagged for, just a silently
# no-op filter. Flagged in the report; left alone per task scope (not an obviously-safe
# one-line fix — unclear whether the intended fix is "wire up an ilike search" or "drop
# the dead parameter", which is a product decision, not a bug fix).

import pytest
from fastapi import HTTPException

import app.routers.requisitions as req_mod
from app.routers.requisitions import (
    RequisitionCreate, RequisitionItemCreate,
    create_requisition, get_requisitions, get_requisition, delete_requisition, health_check,
)


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state):
        self.table_name = table_name
        self.state = state
        self._op = "select"
        self._payload = None
        self._filters = []

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def lte(self, col, val):
        self._filters.append(("lte", col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        self.state["calls"].append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        queue = self.state["responses"].get(self.table_name, [])
        if not queue:
            raise AssertionError(f"no canned response left for table '{self.table_name}'")
        return _Resp(queue.pop(0))


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeQuery(name, self.state)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(responses: dict):
        state = {"calls": [], "responses": {k: list(v) for k, v in responses.items()}}
        monkeypatch.setattr(req_mod, "supabase", _FakeSupabase(state))
        return state
    return _patch


def _create_payload(**overrides):
    base = dict(
        date="2024-03-15", requester="Bob", section="Mechanical", priority="medium",
        status="pending", requisition_number="REQ-1",
        items=[{"description": "Bolt", "cost_per_unit": 2.5, "quantity": 4}],
    )
    base.update(overrides)
    return RequisitionCreate(**base)


# ─── create_requisition ─────────────────────────────────────────────────────────────

async def test_create_requisition_happy_path_with_items(patch_supabase):
    state = patch_supabase({
        "requisitions": [
            [],                                    # duplicate requisition_number check
            [{"id": 1, "requisition_number": "REQ-1"}],  # insert
            [{"id": 1}],                            # all_reqs (for line_number)
        ],
        "requisition_items": [
            [{"id": 10, "description": "Bolt", "cost_per_unit": 2.5, "quantity": 4}],
        ],
    })
    result = await create_requisition(_create_payload(), current_user={"user_id": "u1"})
    assert result["id"] == 1
    assert result["requisition_items"] == [{"id": 10, "description": "Bolt", "cost_per_unit": 2.5, "quantity": 4}]
    assert result["line_number"] == 1

    insert_calls = [c for c in state["calls"] if c["op"] == "insert"]
    assert insert_calls[0]["table"] == "requisitions"
    assert insert_calls[1]["table"] == "requisition_items"
    assert insert_calls[1]["payload"][0]["requisition_id"] == 1


async def test_create_requisition_without_items_sets_empty_list(patch_supabase):
    state = patch_supabase({
        "requisitions": [
            [],
            [{"id": 2, "requisition_number": "REQ-2"}],
            [{"id": 2}],
        ],
    })
    payload = _create_payload(requisition_number="REQ-2", items=[])
    result = await create_requisition(payload, current_user={"user_id": "u1"})
    assert result["requisition_items"] == []
    # requisition_items table must never be touched when there are no items.
    assert all(c["table"] != "requisition_items" for c in state["calls"])


async def test_create_requisition_duplicate_number_is_400(patch_supabase):
    state = patch_supabase({"requisitions": [[{"id": 99}]]})
    with pytest.raises(HTTPException) as exc_info:
        await create_requisition(_create_payload(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 400
    assert "already exists" in exc_info.value.detail
    assert len(state["calls"]) == 1


async def test_create_requisition_insert_failure_is_500(patch_supabase):
    patch_supabase({"requisitions": [[], []]})
    with pytest.raises(HTTPException) as exc_info:
        await create_requisition(_create_payload(), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 500


# ─── get_requisitions ───────────────────────────────────────────────────────────────

async def test_get_requisitions_applies_eq_filters_and_skips_all(patch_supabase):
    state = patch_supabase({"requisitions": [[{"id": 1, "status": "pending"}]]})
    result = await get_requisitions(
        status="pending", priority="all", section=None, requester=None,
        search=None, date_from=None, date_to=None,
    )
    assert result[0]["line_number"] == 1
    call = state["calls"][0]
    assert ("eq", "status", "pending") in call["filters"]
    assert not any(f[0] == "eq" and f[1] == "priority" for f in call["filters"])


async def test_get_requisitions_applies_date_range_filters(patch_supabase):
    from datetime import date
    state = patch_supabase({"requisitions": [[]]})
    await get_requisitions(
        status=None, priority=None, section=None, requester=None, search=None,
        date_from=date(2024, 1, 1), date_to=date(2024, 1, 31),
    )
    call = state["calls"][0]
    assert ("gte", "date", "2024-01-01") in call["filters"]
    assert ("lte", "date", "2024-01-31") in call["filters"]


async def test_get_requisitions_assigns_sequential_line_numbers(patch_supabase):
    patch_supabase({"requisitions": [[{"id": 1}, {"id": 2}, {"id": 3}]]})
    result = await get_requisitions(
        status=None, priority=None, section=None, requester=None,
        search=None, date_from=None, date_to=None,
    )
    assert [r["line_number"] for r in result] == [1, 2, 3]


async def test_get_requisitions_empty_result(patch_supabase):
    patch_supabase({"requisitions": [[]]})
    result = await get_requisitions(
        status=None, priority=None, section=None, requester=None,
        search=None, date_from=None, date_to=None,
    )
    assert result == []


# ─── get_requisition ────────────────────────────────────────────────────────────────

async def test_get_requisition_happy_path_computes_line_number_from_sorted_ids(patch_supabase):
    patch_supabase({
        "requisitions": [
            [{"id": 5, "requisition_number": "REQ-5"}],           # the requested row
            [{"id": 9}, {"id": 3}, {"id": 5}],                    # all ids, unsorted
        ],
    })
    result = await get_requisition(5)
    assert result["requisition_number"] == "REQ-5"
    # Sorted by id: [3, 5, 9] -> id 5 is the 2nd -> line_number 2.
    assert result["line_number"] == 2


async def test_get_requisition_not_found_is_404(patch_supabase):
    patch_supabase({"requisitions": [[]]})
    with pytest.raises(HTTPException) as exc_info:
        await get_requisition(999)
    assert exc_info.value.status_code == 404


# ─── delete_requisition ─────────────────────────────────────────────────────────────

async def test_delete_requisition_happy_path_deletes_items_before_requisition(patch_supabase):
    state = patch_supabase({
        "requisitions": [[{"id": 1}], []],
        "requisition_items": [[]],
    })
    result = await delete_requisition(1, current_user={"user_id": "u1", "role": "manager"})
    assert result == {"success": True, "message": "Requisition deleted successfully"}
    delete_calls = [c for c in state["calls"] if c["op"] == "delete"]
    assert delete_calls[0]["table"] == "requisition_items"
    assert delete_calls[1]["table"] == "requisitions"


async def test_delete_requisition_not_found_is_404(patch_supabase):
    patch_supabase({"requisitions": [[]]})
    with pytest.raises(HTTPException) as exc_info:
        await delete_requisition(999, current_user={"user_id": "u1", "role": "manager"})
    assert exc_info.value.status_code == 404


# ─── health_check ───────────────────────────────────────────────────────────────────

async def test_health_check_healthy(patch_supabase):
    patch_supabase({"requisitions": [[{"id": 1}]]})
    result = await health_check()
    assert result["status"] == "healthy"
    assert result["database"] == "connected"


async def test_health_check_unhealthy_on_db_error(monkeypatch):
    class _RaisingSupabase:
        def table(self, _name):
            raise RuntimeError("db is down")
    monkeypatch.setattr(req_mod, "supabase", _RaisingSupabase())
    result = await health_check()
    assert result["status"] == "unhealthy"
    assert result["database"] == "disconnected"
    assert "error" in result
