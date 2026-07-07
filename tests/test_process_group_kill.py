"""Tests for process-group kill semantics on Shell and Hermes adapters."""

from __future__ import annotations

import os
import signal
import subprocess
import time

import pytest

from agent_workbench.adapters.shell import ShellAdapter
from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.models.harness_run import HarnessRunRepository


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "pgkill.db"
    c = get_connection(str(db))
    apply_migrations(c)
    return c


def _seed(conn):
    ws = WorkspaceRepository(conn).create(tenant_id="t1", name="pgkill")
    conn.commit()
    return ws.workspace_id


def _spawn_long_running(adapter, ws_id, command="sleep 30"):
    return adapter.start(workspace_id=ws_id, session_id="s1", command=command)


class TestShellProcessGroup:
    def test_start_persists_pgid(self, conn):
        ws_id = _seed(conn)
        adapter = ShellAdapter(conn)
        rid = _spawn_long_running(adapter, ws_id)
        hr = HarnessRunRepository(conn).get_by_id(rid)
        assert hr.pgid is not None
        pgid = int(hr.pgid)
        # The pgid should equal the pid (because start_new_session=True
        # makes the child its own group leader).
        assert pgid == int(hr.runtime_process_id)

    def test_stop_kills_entire_process_group(self, conn):
        ws_id = _seed(conn)
        adapter = ShellAdapter(conn)
        rid = _spawn_long_running(adapter, ws_id, command="bash -c 'sleep 30'")
        hr = HarnessRunRepository(conn).get_by_id(rid)
        pgid = int(hr.pgid)
        pid = int(hr.runtime_process_id)
        assert pgid == pid
        # Group should contain the leader.
        members = os.getpgid(pid)
        assert members == pgid
        adapter.stop(rid)
        time.sleep(0.5)
        # Leader should be gone.
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


class TestHermesProcessGroup:
    def test_hermes_start_persists_pgid(self, conn, monkeypatch):
        from agent_workbench.adapters import hermes_adapter as ha

        ws_id = _seed(conn)
        # Build a hermes adapter that doesn't actually require a real binary.
        # We patch shutil.which and subprocess.Popen to return a real sleep
        # process so the pgid semantics are exercised for real.
        monkeypatch.setattr("shutil.which", lambda *_a, **_kw: "/bin/sleep")

        class _FakeAdapter(ha.HermesAdapter):
            def _build_command(self, binary, command, kwargs):
                # Replace hermes with /bin/sleep so we exercise real
                # process-group semantics.
                return ["/bin/sleep", "30"]

        adapter = _FakeAdapter(conn, hermes_binary=ha.LAZY)
        rid = adapter.start(workspace_id=ws_id, session_id="s1", command="x")
        hr = HarnessRunRepository(conn).get_by_id(rid)
        assert hr.pgid is not None
        pgid = int(hr.pgid)
        assert pgid == int(hr.runtime_process_id)

        # Cancel should kill the whole group.
        adapter.cancel(rid)
        time.sleep(0.5)
        with pytest.raises(ProcessLookupError):
            os.kill(int(hr.runtime_process_id), 0)
