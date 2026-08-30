# tests/test_contractors_jobs.py — contractors.py's own hand-written endpoints (beyond
# the generic CrudRouter base already proven by test_crud_router.py): get_contractor
# (merges in the contractor's jobs), and the contractor_jobs sub-resource CRUD.

import pytest
from fastapi import HTTPException

import app.routers.contractors as contractors_mod
from app.routers.contractors import (
    get_contractor, get_all_jobs, create_job, update_job, delete_job, ContractorJobCreate, ContractorJobUpdate,
)


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, response_data, row_exists=True):
        self._response = response_data
        self._row_exists = row_exists

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self

    def update(self, data):
        self._response = [{"id": 1, **data}] if self._row_exists else []
        return self

    def insert(self, data):
        self._response = [{"id": "new", **data}]
        return self

    def delete(self): return self
    def execute(self): return _Resp(self._response)


class _FakeSupabase:
    def __init__(self, tables: dict, row_exists=True):
        self._tables = tables
        self._row_exists = row_exists

    def table(self, name):
        return _FakeQuery(self._tables.get(name, []), self._row_exists)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _patch(tables, row_exists=True):
        monkeypatch.setattr(contractors_mod, "supabase", _FakeSupabase(tables, row_exists))
    return _patch


async def test_get_contractor_merges_in_jobs(patch_supabase):
    patch_supabase({
        "contractors": [{"id": 1, "company_name": "Acme Builders"}],
        "contractor_jobs": [{"id": 10, "contractor_id": 1, "status": "active"}],
    })
    result = await get_contractor(1)
    assert result["company_name"] == "Acme Builders"
    assert result["jobs"] == [{"id": 10, "contractor_id": 1, "status": "active"}]


async def test_get_contractor_missing_is_404(patch_supabase):
    patch_supabase({"contractors": [], "contractor_jobs": []})
    with pytest.raises(HTTPException) as exc_info:
        await get_contractor(999)
    assert exc_info.value.status_code == 404


async def test_get_all_jobs_returns_rows(patch_supabase):
    patch_supabase({"contractor_jobs": [{"id": 1}, {"id": 2}]})
    result = await get_all_jobs()
    assert len(result) == 2


async def test_create_job_returns_inserted_row(patch_supabase):
    patch_supabase({"contractor_jobs": []})
    data = ContractorJobCreate(contractor_id=1, job_title="Roof repair", start_date="2024-01-01", status="active")
    result = await create_job(data, current_user={"user_id": "u1"})
    assert result["job_title"] == "Roof repair"


async def test_update_job_not_found_is_404(patch_supabase):
    patch_supabase({"contractor_jobs": []}, row_exists=False)
    with pytest.raises(HTTPException) as exc_info:
        await update_job(999, ContractorJobUpdate(status="completed"), current_user={"user_id": "u1"})
    assert exc_info.value.status_code == 404


async def test_update_job_success(patch_supabase):
    patch_supabase({"contractor_jobs": []})
    result = await update_job(1, ContractorJobUpdate(status="completed"), current_user={"user_id": "u1"})
    assert result["status"] == "completed"


async def test_delete_job(patch_supabase):
    patch_supabase({"contractor_jobs": []})
    result = await delete_job(1, current_user={"user_id": "u1", "role": "manager"})
    assert result == {"ok": True}
