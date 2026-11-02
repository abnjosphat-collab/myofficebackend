"""A slow synchronous database save must not block the ASGI event loop."""

import asyncio
from contextvars import ContextVar
import threading
from types import SimpleNamespace
import httpx
import pytest
from fastapi import HTTPException

from app.routers import overtime


async def test_create_leaves_event_loop_available_during_database_save(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    actor_probe = ContextVar('overtime_save_actor_probe', default=None)
    observed = []

    class Query:
        def insert(self, payload):
            self.payload = payload
            return self

        def execute(self):
            observed.append((threading.get_ident(), actor_probe.get()))
            entered.set()
            release.wait(0.5)  # finite even on the pre-fix, blocked event loop
            return SimpleNamespace(data=[{'id': 1, **self.payload}])

    monkeypatch.setattr(overtime, 'supabase', SimpleNamespace(table=lambda name: Query()))
    monkeypatch.setattr(overtime, '_overlapping_request', lambda *args: None)
    payload = overtime.OvertimeCreate(employee_name='Ann Alpha', employee_id='C001', position='Electrician',
                                      overtime_type='normal', date='2026-11-02', start_time='17:00', end_time='19:00')
    token = actor_probe.set('fixture-actor')
    task = asyncio.create_task(overtime.create_overtime(payload, current_user={'role': 'admin'}))
    try:
        assert await asyncio.to_thread(entered.wait, 2), 'The mocked save did not start'
        assert not task.done(), 'The event loop was blocked until the synchronous save completed'
    finally:
        release.set()
        result = await task
        actor_probe.reset(token)
    assert result['id'] == 1
    assert observed == [(observed[0][0], 'fixture-actor')]
    assert observed[0][0] != threading.get_ident()


@pytest.mark.parametrize('operation', ['list', 'create'])
async def test_transport_outage_is_service_unavailable_without_replaying_a_save(monkeypatch, operation):
    calls = []

    def unavailable(name):
        calls.append(name)
        raise httpx.ReadError('[Errno 11] Resource temporarily unavailable')

    monkeypatch.setattr(overtime, 'supabase', SimpleNamespace(table=unavailable))
    with pytest.raises(HTTPException) as caught:
        if operation == 'list':
            await overtime.get_overtime(status=None, overtime_type=None, employee_id=None, current_user={'role': 'admin'})
        else:
            payload = overtime.OvertimeCreate(employee_name='Ann Alpha', employee_id='C001', position='Electrician',
                                              overtime_type='normal', date='2026-11-02', hours=2)
            await overtime.create_overtime(payload, current_user={'role': 'admin'})
    assert caught.value.status_code == 503
    assert 'ref ' in caught.value.detail
    assert 'Errno' not in caught.value.detail
    assert len(calls) == 1
