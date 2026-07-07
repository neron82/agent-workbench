"""Live-binary integration test for :mod:`agent_workbench.adapters.opencode`.

This test is intentionally narrow: it proves that the real local
``opencode`` binary can be discovered, started via ``opencode serve`` as
one server per HarnessRun, and then stopped cleanly through the product
adapter.

The test only runs when the binary exists at the host-local path used in
this environment. In environments without that binary it is skipped
rather than faking success.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agent_workbench.adapters.opencode import OpencodeAdapter
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.workspace import WorkspaceRepository


LIVE_OPCODE_PATH = Path("/home/neron/.opencode/bin/opencode")


@pytest.mark.skipif(
    not LIVE_OPCODE_PATH.exists() or not os.access(LIVE_OPCODE_PATH, os.X_OK),
    reason="live opencode binary not available on this host",
)
def test_live_opencode_binary_start_stop(db):
    ws_repo = WorkspaceRepository(db)
    hr_repo = HarnessRunRepository(db)
    workspace = ws_repo.create(tenant_id="tenant-live", name="Live Opencode Workspace")

    adapter = OpencodeAdapter(db)
    env = dict(os.environ)
    env["PATH"] = f"{LIVE_OPCODE_PATH.parent}:{env.get('PATH', '')}"

    harness_run_id = adapter.start(
        workspace_id=workspace.workspace_id,
        session_id="session-live-opencode",
        command="say hello",
        cwd="/home/neron/projects/agent-workbench",
        env=env,
    )

    proc = adapter._sessions[harness_run_id]["process"]

    try:
        hr = hr_repo.get_by_id(harness_run_id)
        assert hr is not None
        assert hr.harness_type == "opencode"
        assert hr.status == "running"
        assert hr.runtime_process_id is not None
        assert proc.poll() is None

        # Give the headless server a brief moment to fail fast if the
        # binary is unusable under the current environment.
        time.sleep(1.0)
        assert proc.poll() is None
    finally:
        if proc.poll() is None:
            adapter.stop(harness_run_id)

    hr = hr_repo.get_by_id(harness_run_id)
    assert hr is not None
    assert hr.status == "completed"
    assert hr.ended_at is not None
