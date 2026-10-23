# tests/test_track_migration.py — the tracker's refuse list (F-06): retired
# files are never "pending" and --mark-applied refuses them. No live DB is
# touched: the module's supabase handle is replaced with a recording fake.

import importlib.util
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _load_tracker(monkeypatch=None):
    spec = importlib.util.spec_from_file_location(
        "track_migration_under_test", BACKEND_ROOT / "scripts" / "track_migration.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingTable:
    def __init__(self, state):
        self.state = state

    def select(self, *a, **k):
        return self

    def upsert(self, row, on_conflict=None):
        self.state.setdefault("upserts", []).append(row)
        return self

    def execute(self):
        class _Resp:
            data = []
        return _Resp()


class _RecordingSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        assert name == "schema_migrations"
        return _RecordingTable(self.state)


RETIRED = "supabase_migration_maintenance_schedules.sql"


def test_refuse_list_names_the_schedules_rebuild():
    tracker = _load_tracker()
    assert RETIRED in tracker.RETIRED_DO_NOT_RERUN


def test_mark_applied_refuses_a_retired_file(capsys):
    tracker = _load_tracker()
    state = {}
    tracker.supabase = _RecordingSupabase(state)
    with pytest.raises(SystemExit) as exc:
        tracker.cmd_mark_applied(RETIRED, None)
    assert exc.value.code == 1
    assert "upserts" not in state  # refused before any database call
    out = capsys.readouterr().out.lower()
    assert "never re-run" in out or "never re run" in out.replace("-", " ")


def test_list_shows_retired_not_pending(capsys):
    tracker = _load_tracker()
    tracker.supabase = _RecordingSupabase({})
    tracker.cmd_list()
    out = capsys.readouterr().out
    retired_line = next(line for line in out.splitlines() if RETIRED in line)
    assert "PENDING" not in retired_line
    assert "RETIRED" in retired_line


def test_ordinary_files_still_list_as_pending(capsys):
    # The refuse list must not swallow normal files: with an empty applied
    # table, a non-retired migration still shows as pending.
    tracker = _load_tracker()
    tracker.supabase = _RecordingSupabase({})
    tracker.cmd_list()
    out = capsys.readouterr().out
    assert "[PENDING]" in out
