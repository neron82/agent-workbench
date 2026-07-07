"""Tests for ShellAdapter — lifecycle, capabilities, process tracking, cancel."""

from __future__ import annotations

import sqlite3
import time

import pytest

from agent_workbench.adapters.shell import ShellAdapter
from agent_workbench.adapters.base import AdapterCapabilities
from agent_workbench.models.workspace import WorkspaceRepository


def _seed_workspace(db: sqlite3.Connection) -> str:
    repo = WorkspaceRepository(db)
    ws = repo.create(tenant_id="test", name="test-ws")
    return ws.workspace_id


class TestShellCapabilities:
    def test_adapter_type(self, db):
        adapter = ShellAdapter(db)
        assert adapter.adapter_type == "shell"

    def test_capabilities(self, db):
        adapter = ShellAdapter(db)
        caps = adapter.capabilities
        assert caps.can_stop is True
        assert caps.can_cancel is True
        assert caps.can_shell is True
        assert caps.can_file_write is True
        assert caps.has_process_ids is True
        assert caps.can_replay is True
        assert caps.can_pause is False
        assert caps.can_steer is False
        assert caps.can_diff is False
        assert caps.can_remote is False


class TestShellLifecycle:
    def test_start_creates_harness_run(self, db):
        ws_id = _seed_workspace(db)
        adapter = ShellAdapter(db)
        hr_id = adapter.start(
            workspace_id=ws_id,
            session_id="sess-1",
            command="echo hello",
        )
        assert hr_id is not None
        # Wait for completion
        time.sleep(1)
        from agent_workbench.models.harness_run import HarnessRunRepository
        repo = HarnessRunRepository(db)
        hr = repo.get_by_id(hr_id)
        assert hr is not None
        assert hr.harness_type == "shell"
        assert hr.status in ("running", "completed", "failed")

    def test_get_runtime_ids(self, db):
        ws_id = _seed_workspace(db)
        adapter = ShellAdapter(db)
        hr_id = adapter.start(
            workspace_id=ws_id,
            session_id="sess-2",
            command="echo test",
        )
        time.sleep(0.5)
        ids = adapter.get_runtime_ids(hr_id)
        assert ids.process_id is not None

    def test_get_transcript(self, db):
        ws_id = _seed_workspace(db)
        adapter = ShellAdapter(db)
        hr_id = adapter.start(
            workspace_id=ws_id,
            session_id="sess-3",
            command="echo hello_world",
        )
        time.sleep(1)
        transcript = adapter.get_transcript(hr_id)
        assert "hello_world" in transcript.stdout

    def test_cancel(self, db):
        ws_id = _seed_workspace(db)
        adapter = ShellAdapter(db)
        hr_id = adapter.start(
            workspace_id=ws_id,
            session_id="sess-4",
            command="sleep 30",
        )
        time.sleep(0.5)
        adapter.cancel(hr_id)
        time.sleep(0.5)
        from agent_workbench.models.harness_run import HarnessRunRepository
        repo = HarnessRunRepository(db)
        hr = repo.get_by_id(hr_id)
        assert hr.status == "cancelled"