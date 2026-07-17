"""Shell harness adapter — executes local commands via subprocess.

This version (2026-07-05):
- Spawns each command in a fresh process group (``preexec_fn=os.setsid``)
  so that ``stop``/``cancel`` can deliver signals to the whole group,
  not just the immediate shell.
- Persists every stdout/stderr line to ``harness_transcripts`` while the
  process runs, so the run-detail page can show the real transcript
  even after a server restart.
- Records the final exit code / signal on the ``harness_runs`` row.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from agent_workbench.adapters._shared import (
    collect_output_daemon,
    db_path_from_conn,
    terminate_process_group,
)
from agent_workbench.adapters.base import (
    AdapterCapabilities,
    BaseAdapter,
    HarnessProcessError,
    RuntimeIds,
    Transcript,
)
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.services import TranscriptService


class ShellAdapter(BaseAdapter):
    """Local shell command adapter.

    Capabilities:
    - can_stop=True (SIGTERM to the process group)
    - can_cancel=True (SIGKILL to the process group)
    - can_shell=True
    - can_file_write=True
    - has_process_ids=True
    - can_replay=True (via durable transcript)
    - can_pause=False, can_steer=False, can_diff=False, can_remote=False
    """

    adapter_type = "shell"
    capabilities = AdapterCapabilities(
        can_stop=True,
        can_cancel=True,
        can_shell=True,
        can_file_write=True,
        has_process_ids=True,
        can_replay=True,
    )

    def __init__(self, conn):
        super().__init__(conn)
        self._repo = HarnessRunRepository(conn)
        self._transcripts = TranscriptService()
        self._db_path = db_path_from_conn(conn)
        # {harness_run_id: {"process": Popen, "stdout": str, "stderr": str, "pgid": int}}
        self._processes: Dict[str, Dict[str, Any]] = {}
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
        # 1. Create HarnessRun record
        hr = self._repo.create(
            workspace_id=workspace_id,
            session_id=session_id,
            harness_type=self.adapter_type,
            task_spec_id=task_spec_id,
            status="starting",
            control_capabilities=self.capabilities_dict(),
        )
        harness_run_id = hr.harness_run_id

        # 2. Record the start event in the durable event log
        self._transcripts.append_event(
            self.conn,
            harness_run_id=harness_run_id,
            event_type="start",
            detail={"command": command, "cwd": kwargs.get("cwd")},
        )

        try:
            # 3. Spawn command in a fresh process group. ``start_new_session``
            #    is the cross-platform way to get the same effect as
            #    ``os.setsid`` inside the child.
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=kwargs.get("cwd", None),
                env=kwargs.get("env", None),
                start_new_session=True,
            )
        except Exception as e:
            self._repo.update_status(
                harness_run_id,
                status="failed",
                ended_at=time.time(),
            )
            self._transcripts.append_event(
                self.conn,
                harness_run_id=harness_run_id,
                event_type="exit",
                detail={"error": str(e)},
            )
            raise HarnessProcessError(str(e)) from e

        # 4. Capture process group id
        try:
            pgid = os.getpgid(process.pid)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = None

        # 5. Register the process in memory + persist runtime ids
        self._processes[harness_run_id] = {
            "process": process,
            "stdout": "",
            "stderr": "",
            "command": command,
            "pgid": pgid,
        }
        self._repo.update_runtime_ids(
            harness_run_id,
            runtime_process_id=str(process.pid),
            pgid=str(pgid) if pgid is not None else None,
        )
        self._repo.update_status(
            harness_run_id,
            status="running",
            started_at=time.time(),
        )

        # 6. Collect output in a background thread
        t = threading.Thread(
            target=collect_output_daemon,
            args=(harness_run_id, process, self._db_path, self._processes),
            daemon=True,
        )
        self._collect_threads[harness_run_id] = t
        t.start()

        return harness_run_id

    # ------------------------------------------------------------------
    # Stop / Cancel
    # ------------------------------------------------------------------

    def stop(self, harness_run_id: str) -> None:
        """SIGTERM the entire process group, falling back to the leader PID."""
        terminate_process_group(
            harness_run_id,
            signal.SIGTERM,
            terminal_status="stopping",
            sessions_dict=self._processes,
            repo=self._repo,
            transcripts=self._transcripts,
            conn=self.conn,
        )

    def cancel(self, harness_run_id: str) -> None:
        """SIGKILL the entire process group, falling back to the leader PID."""
        terminate_process_group(
            harness_run_id,
            signal.SIGKILL,
            terminal_status="cancelled",
            sessions_dict=self._processes,
            repo=self._repo,
            transcripts=self._transcripts,
            conn=self.conn,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_runtime_ids(self, harness_run_id: str) -> RuntimeIds:
        info = self._processes.get(harness_run_id)
        if info:
            proc = info["process"]
            pid = str(proc.pid) if proc else None
            return RuntimeIds(process_id=pid)
        run = self._repo.get_by_id(harness_run_id)
        if run is None:
            return RuntimeIds()
        return RuntimeIds(process_id=run.runtime_process_id)

    def get_transcript(self, harness_run_id: str) -> Transcript:
        """Return the *in-memory* transcript for backward compatibility.

        For the durable view, prefer ``TranscriptService.list``.
        """
        info = self._processes.get(harness_run_id)
        if not info:
            return Transcript()
        return Transcript(stdout=info["stdout"], stderr=info["stderr"])
