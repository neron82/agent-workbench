"""Live SSH integration test for :mod:`agent_workbench.adapters.ssh`.

This test exercises the real local SSH alias ``mbp`` from this host.
It is intentionally narrow but end-to-end: start a remote command,
verify runtime IDs and transcript visibility, then stop the remote
process through the product adapter and confirm the run is finalized.

If the alias is not available in the current environment, the test is
skipped rather than faking success.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from agent_workbench.adapters.ssh import SshAdapter
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.workspace import WorkspaceRepository


def _live_ssh_available() -> bool:
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                "mbp",
                "hostname",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _live_ssh_available(), reason="live ssh alias 'mbp' not available on this host")
def test_live_ssh_start_transcript_stop(db):
    ws_repo = WorkspaceRepository(db)
    hr_repo = HarnessRunRepository(db)
    workspace = ws_repo.create(tenant_id="tenant-live", name="Live SSH Workspace")

    adapter = SshAdapter(db)
    marker = f"ssh-live-{int(time.time() * 1000)}"

    harness_run_id = adapter.start(
        workspace_id=workspace.workspace_id,
        session_id="session-live-ssh",
        command=f"echo {marker}; sleep 30",
        remote_host="mbp",
    )

    hr = hr_repo.get_by_id(harness_run_id)
    assert hr is not None
    assert hr.harness_type == "ssh"
    assert hr.status == "running"
    assert hr.runtime_remote_process_id is not None
    assert hr.runtime_remote_process_id.startswith("mbp:")

    ids = adapter.get_runtime_ids(harness_run_id)
    assert ids.remote_host == "mbp"
    assert ids.remote_pid

    # Give the remote log a moment to flush.
    time.sleep(1.0)
    transcript = adapter.get_transcript(harness_run_id)
    assert marker in transcript.stdout

    adapter.stop(harness_run_id)
    time.sleep(1.0)

    hr = hr_repo.get_by_id(harness_run_id)
    assert hr is not None
    assert hr.status == "completed"
    assert hr.ended_at is not None

    # After a successful stop, reconnect-and-reap should have nothing left to kill.
    assert adapter.reconnect_and_reap(harness_run_id) is False
