"""Tests for SshAdapter — lifecycle, capabilities, remote process tracking, reap.

These tests mock SSH calls since no SSH host is available in the test environment.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from agent_workbench.adapters.ssh import SshAdapter
from agent_workbench.adapters.base import AdapterCapabilities
from agent_workbench.models.workspace import WorkspaceRepository


def _seed_workspace(db: sqlite3.Connection) -> str:
    repo = WorkspaceRepository(db)
    ws = repo.create(tenant_id="test", name="test-ws")
    return ws.workspace_id


class TestSshCapabilities:
    def test_adapter_type(self, db):
        adapter = SshAdapter(db)
        assert adapter.adapter_type == "ssh"

    def test_capabilities(self, db):
        adapter = SshAdapter(db)
        caps = adapter.capabilities
        assert caps.can_stop is True
        assert caps.can_cancel is True
        assert caps.can_shell is True
        assert caps.can_remote is True
        assert caps.can_file_write is True
        assert caps.has_process_ids is True
        assert caps.can_replay is True
        assert caps.can_pause is False
        assert caps.can_steer is False
        assert caps.can_diff is False


class TestSshLifecycle:
    def test_start_requires_remote_host(self, db):
        ws_id = _seed_workspace(db)
        adapter = SshAdapter(db)
        with pytest.raises(Exception):
            adapter.start(
                workspace_id=ws_id,
                session_id="sess-1",
                command="echo hello",
                remote_host="",  # empty -> should raise
            )

    @patch("agent_workbench.adapters.ssh.subprocess.Popen")
    def test_start_creates_harness_run(self, mock_popen, db):
        ws_id = _seed_workspace(db)
        adapter = SshAdapter(db)

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.communicate.return_value = ("99999\n", "")
        mock_popen.return_value = mock_proc

        hr_id = adapter.start(
            workspace_id=ws_id,
            session_id="sess-2",
            command="echo hello",
            remote_host="test.example.com",
        )
        assert hr_id is not None

        from agent_workbench.models.harness_run import HarnessRunRepository
        repo = HarnessRunRepository(db)
        hr = repo.get_by_id(hr_id)
        assert hr is not None
        assert hr.harness_type == "ssh"
        assert hr.runtime_remote_process_id == "test.example.com:99999"

    @patch("agent_workbench.adapters.ssh.subprocess.Popen")
    @patch("agent_workbench.adapters.ssh.subprocess.run")
    def test_stop_marks_completed_when_remote_process_exits(self, mock_run, mock_popen, db):
        ws_id = _seed_workspace(db)
        adapter = SshAdapter(db)

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.communicate.return_value = ("99999\n", "")
        mock_popen.return_value = mock_proc

        mock_run.side_effect = [
            MagicMock(stdout="", stderr=""),
            MagicMock(stdout="DEAD\n", stderr=""),
        ]

        hr_id = adapter.start(
            workspace_id=ws_id,
            session_id="sess-stop",
            command="sleep 30",
            remote_host="test.example.com",
        )

        adapter.stop(hr_id)

        from agent_workbench.models.harness_run import HarnessRunRepository
        repo = HarnessRunRepository(db)
        hr = repo.get_by_id(hr_id)
        assert hr is not None
        assert hr.status == "completed"
        assert hr.ended_at is not None

    @patch("agent_workbench.adapters.ssh.subprocess.Popen")
    @patch("agent_workbench.adapters.ssh.subprocess.run")
    def test_cancel(self, mock_run, mock_popen, db):
        ws_id = _seed_workspace(db)
        adapter = SshAdapter(db)

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.communicate.return_value = ("99999\n", "")
        mock_popen.return_value = mock_proc
        mock_run.return_value = MagicMock(stdout="", stderr="")

        hr_id = adapter.start(
            workspace_id=ws_id,
            session_id="sess-3",
            command="sleep 30",
            remote_host="test.example.com",
        )
        adapter.cancel(hr_id)

        from agent_workbench.models.harness_run import HarnessRunRepository
        repo = HarnessRunRepository(db)
        hr = repo.get_by_id(hr_id)
        assert hr.status == "cancelled"

    @patch("agent_workbench.adapters.ssh.subprocess.Popen")
    @patch("agent_workbench.adapters.ssh.subprocess.run")
    def test_reconnect_and_reap_alive(self, mock_run, mock_popen, db):
        ws_id = _seed_workspace(db)
        adapter = SshAdapter(db)

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.communicate.return_value = ("99999\n", "")
        mock_popen.return_value = mock_proc

        # First call: check returns ALIVE, second: kill
        mock_run.side_effect = [
            MagicMock(stdout="ALIVE\n", stderr=""),
            MagicMock(stdout="", stderr=""),
        ]

        hr_id = adapter.start(
            workspace_id=ws_id,
            session_id="sess-4",
            command="sleep 60",
            remote_host="test.example.com",
        )
        result = adapter.reconnect_and_reap(hr_id)
        assert result is True

        from agent_workbench.models.harness_run import HarnessRunRepository
        repo = HarnessRunRepository(db)
        hr = repo.get_by_id(hr_id)
        assert hr.status == "cancelled"

    @patch("agent_workbench.adapters.ssh.subprocess.Popen")
    @patch("agent_workbench.adapters.ssh.subprocess.run")
    def test_reconnect_and_reap_dead(self, mock_run, mock_popen, db):
        ws_id = _seed_workspace(db)
        adapter = SshAdapter(db)

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.communicate.return_value = ("99999\n", "")
        mock_popen.return_value = mock_proc
        mock_run.return_value = MagicMock(stdout="DEAD\n", stderr="")

        hr_id = adapter.start(
            workspace_id=ws_id,
            session_id="sess-5",
            command="echo done",
            remote_host="test.example.com",
        )
        result = adapter.reconnect_and_reap(hr_id)
        assert result is False

    @patch("agent_workbench.adapters.ssh.subprocess.Popen")
    def test_get_runtime_ids(self, mock_popen, db):
        ws_id = _seed_workspace(db)
        adapter = SshAdapter(db)

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.communicate.return_value = ("77777\n", "")
        mock_popen.return_value = mock_proc

        hr_id = adapter.start(
            workspace_id=ws_id,
            session_id="sess-6",
            command="echo test",
            remote_host="remote.example.com",
        )
        ids = adapter.get_runtime_ids(hr_id)
        assert ids.remote_host == "remote.example.com"
        assert ids.remote_pid == "77777"