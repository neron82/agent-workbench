"""Opencode harness adapter — one server instance per HarnessRun."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from agent_workbench.adapters._shared import (
    collect_output_daemon,
    db_path_from_conn,
)
from agent_workbench.adapters.base import (
    AdapterCapabilities,
    BaseAdapter,
    HarnessNotReadyError,
    HarnessProcessError,
    RuntimeIds,
    Transcript,
)
from agent_workbench.models.harness_run import HarnessRunRepository


class OpencodeAdapter(BaseAdapter):
    """Adapter for the Opencode code-agent harness.

    Lifecycle (decision 3): one Opencode server per HarnessRun / worker task.
    No shared global server as the default.

    Capabilities:
    - can_stop=True (abort session)
    - can_cancel=True (terminate server)
    - can_diff=True (code-diff capture)
    - can_shell=True (shell tool within opencode session)
    - can_file_write=True (file-write tool within opencode session)
    - has_process_ids=True (server PID tracked)
    - can_replay=True (transcript replay)
    - can_pause=False, can_steer=False, can_remote=False
    """

    adapter_type = "opencode"
    capabilities = AdapterCapabilities(
        can_stop=True,
        can_cancel=True,
        can_diff=True,
        can_shell=True,
        can_file_write=True,
        has_process_ids=True,
        can_replay=True,
    )

    def __init__(self, conn) -> None:
        super().__init__(conn)
        self._repo = HarnessRunRepository(conn)
        self._db_path = db_path_from_conn(conn)
        # {harness_run_id: {"process": Popen, "session_id": str, "stdout": str, "stderr": str, "diff": str}}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        # Track daemon reader threads so tests can join them.
        self._collect_threads: Dict[str, threading.Thread] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        workspace_id: str,
        session_id: str,
        command: str,
        task_spec_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Start an Opencode server for this HarnessRun.

        Raises ConnectionError if the 'opencode' binary is not found.
        """
        # 1. Check binary availability. If the caller provided an
        # explicit env mapping, honour its PATH when resolving the
        # executable; otherwise fall back to the current process PATH.
        child_env = kwargs.get("env", None)
        lookup_path = None
        if isinstance(child_env, dict):
            lookup_path = child_env.get("PATH")
        opencode_bin = shutil.which("opencode", path=lookup_path)
        if opencode_bin is None:
            raise ConnectionError(
                "opencode binary not found in PATH. "
                "Install opencode or add its location to PATH to use this adapter."
            )

        # 2. Create HarnessRun record
        hr = self._repo.create(
            workspace_id=workspace_id,
            session_id=session_id,
            harness_type=self.adapter_type,
            task_spec_id=task_spec_id,
            status="starting",
            control_capabilities=self.capabilities_dict(),
        )
        harness_run_id = hr.harness_run_id

        try:
            # 3. Spawn 'opencode serve' subprocess
            # NOTE: Actual command-line flags depend on the opencode CLI version.
            # This is the canonical invocation; adapt flags as the CLI evolves.
            process = subprocess.Popen(
                ["opencode", "serve"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=kwargs.get("cwd", None),
                env=kwargs.get("env", None),
                start_new_session=True,
            )

            # 4. Register session
            self._sessions[harness_run_id] = {
                "process": process,
                "session_id": session_id,
                "stdout": "",
                "stderr": "",
                "diff": "",
                "command": command,
            }

            # 5. Update runtime IDs and status
            self._repo.update_runtime_ids(
                harness_run_id,
                runtime_session_id=session_id,
                runtime_process_id=str(process.pid),
            )
            self._repo.update_status(
                harness_run_id,
                status="running",
                started_at=time.time(),
            )

            # 6. Spawn daemon reader so the transcript reflects real
            # server logs.
            t = threading.Thread(
                target=collect_output_daemon,
                args=(harness_run_id, process, self._db_path, self._sessions),
                daemon=True,
            )
            self._collect_threads[harness_run_id] = t
            t.start()

            return harness_run_id

        except Exception as e:
            self._repo.update_status(
                harness_run_id,
                status="failed",
                ended_at=time.time(),
            )
            raise HarnessProcessError(str(e)) from e

    def stop(self, harness_run_id: str) -> None:
        """Graceful stop — abort the Opencode session and terminate the server."""
        info = self._sessions.get(harness_run_id)
        if not info:
            raise HarnessNotReadyError(f"No session for {harness_run_id}")

        proc = info["process"]
        if proc.poll() is not None:
            # Already exited — just update status
            self._repo.update_status(
                harness_run_id,
                status="completed",
                ended_at=time.time(),
            )
            return

        try:
            proc.terminate()
            proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()
            proc.wait()

        self._repo.update_status(
            harness_run_id,
            status="completed",
            ended_at=time.time(),
        )

    def cancel(self, harness_run_id: str) -> None:
        """Forceful cancel — kill the Opencode server immediately."""
        info = self._sessions.get(harness_run_id)
        if not info:
            raise HarnessNotReadyError(f"No session for {harness_run_id}")

        proc = info["process"]
        if proc.poll() is not None:
            self._repo.update_status(
                harness_run_id,
                status="cancelled",
                ended_at=time.time(),
            )
            return

        try:
            proc.kill()
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass

        self._repo.update_status(
            harness_run_id,
            status="cancelled",
            ended_at=time.time(),
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_runtime_ids(self, harness_run_id: str) -> RuntimeIds:
        """Return current runtime identifiers for this Opencode session."""
        info = self._sessions.get(harness_run_id)
        if not info:
            return RuntimeIds()
        proc = info["process"]
        pid = str(proc.pid) if proc else None
        return RuntimeIds(
            session_id=info.get("session_id"),
            process_id=pid,
        )

    def get_transcript(self, harness_run_id: str) -> Transcript:
        """Return captured stdout/stderr for this Opencode session."""
        info = self._sessions.get(harness_run_id)
        if not info:
            return Transcript()
        return Transcript(stdout=info["stdout"], stderr=info["stderr"])

    def get_diff(self, harness_run_id: str) -> str:
        """Return the code diff captured during this Opencode session.

        This method is specific to OpencodeAdapter — not part of the
        abstract BaseAdapter contract.
        """
        info = self._sessions.get(harness_run_id)
        if not info:
            return ""
        return info.get("diff", "")
