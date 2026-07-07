"""SSH harness adapter — executes commands on remote hosts via system ssh."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any, Dict, Optional

from agent_workbench.adapters.base import (
    AdapterCapabilities,
    BaseAdapter,
    HarnessNotReadyError,
    HarnessProcessError,
    RuntimeIds,
    Transcript,
)
from agent_workbench.models.harness_run import HarnessRunRepository


class SshAdapter(BaseAdapter):
    """Remote SSH command adapter.

    Capabilities:
    - can_stop=True (SIGTERM via SSH)
    - can_cancel=True (best-effort SIGKILL via SSH)
    - can_shell=True
    - can_remote=True
    - can_file_write=True (via scp)
    - has_process_ids=True
    - can_replay=True (via log review)
    - can_pause=False, can_steer=False, can_diff=False

    Tracks remote process identity (remote_host + remote_pid).
    Implements reconnect_and_reap() for orphan cleanup (decision 8).
    """

    adapter_type = "ssh"
    capabilities = AdapterCapabilities(
        can_stop=True,
        can_cancel=True,
        can_shell=True,
        can_file_write=True,
        can_remote=True,
        has_process_ids=True,
        can_replay=True,
    )

    def __init__(self, conn):
        super().__init__(conn)
        self._repo = HarnessRunRepository(conn)
        self._runs: Dict[str, Dict[str, Any]] = {}

    def start(
        self,
        *,
        workspace_id: str,
        session_id: str,
        command: str,
        task_spec_id: Optional[str] = None,
        remote_host: str = "",
        ssh_user: Optional[str] = None,
        ssh_key: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        if not remote_host:
            raise HarnessNotReadyError("remote_host is required for SshAdapter")

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
            # Build SSH command to run remote command in background and echo PID
            ssh_parts = ["ssh"]
            if ssh_user:
                ssh_parts.extend(["-l", ssh_user])
            if ssh_key:
                ssh_parts.extend(["-i", ssh_key])
            ssh_parts.extend([
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ControlMaster=auto",
                "-o", "ControlPath=/tmp/aw_ssh_%C",
                "-o", "ControlPersist=300",
                remote_host,
            ])

            # Run command in background, capture PID
            remote_cmd = f"nohup bash -c '{command}' > /tmp/aw_run_{harness_run_id}.log 2>&1 & echo $!"
            full_ssh = ssh_parts + [remote_cmd]

            proc = subprocess.Popen(
                full_ssh,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Wait for PID output (should be quick)
            try:
                stdout, stderr = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()

            remote_pid = stdout.strip().split("\n")[0] if stdout else ""

            self._runs[harness_run_id] = {
                "remote_host": remote_host,
                "remote_pid": remote_pid,
                "ssh_user": ssh_user,
                "ssh_key": ssh_key,
                "command": command,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "local_proc": proc,
            }

            self._repo.update_runtime_ids(
                harness_run_id,
                runtime_process_id=str(proc.pid),
                runtime_remote_process_id=f"{remote_host}:{remote_pid}",
            )
            self._repo.update_status(
                harness_run_id,
                status="running",
                started_at=time.time(),
            )

            return harness_run_id

        except Exception as e:
            self._repo.update_status(
                harness_run_id,
                status="failed",
                ended_at=time.time(),
            )
            raise HarnessProcessError(str(e)) from e

    def stop(self, harness_run_id: str) -> None:
        info = self._runs.get(harness_run_id)
        if not info:
            raise HarnessNotReadyError(f"No run for {harness_run_id}")
        remote_host = info["remote_host"]
        remote_pid = info["remote_pid"]
        ssh_user = info.get("ssh_user")
        ssh_key = info.get("ssh_key")

        ssh_parts = ["ssh", "-o", "ControlPath=/tmp/aw_ssh_%C"]
        if ssh_user:
            ssh_parts.extend(["-l", ssh_user])
        if ssh_key:
            ssh_parts.extend(["-i", ssh_key])

        try:
            subprocess.run(
                ssh_parts + [remote_host, f"kill -TERM {remote_pid} 2>/dev/null; true"],
                capture_output=True,
                timeout=15,
            )
        except Exception:
            pass

        # Give the remote process a brief grace period to exit.
        for _ in range(10):
            try:
                result = subprocess.run(
                    ssh_parts + [remote_host, f"kill -0 {remote_pid} 2>/dev/null && echo ALIVE || echo DEAD"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if "DEAD" in result.stdout:
                    self._repo.update_status(
                        harness_run_id,
                        status="completed",
                        ended_at=time.time(),
                    )
                    return
            except Exception:
                break
            time.sleep(0.2)

        self._repo.update_status(harness_run_id, status="stopping")

    def cancel(self, harness_run_id: str) -> None:
        info = self._runs.get(harness_run_id)
        if not info:
            raise HarnessNotReadyError(f"No run for {harness_run_id}")
        remote_host = info["remote_host"]
        remote_pid = info["remote_pid"]
        ssh_user = info.get("ssh_user")
        ssh_key = info.get("ssh_key")

        ssh_parts = ["ssh", "-o", "ControlPath=/tmp/aw_ssh_%C"]
        if ssh_user:
            ssh_parts.extend(["-l", ssh_user])
        if ssh_key:
            ssh_parts.extend(["-i", ssh_key])
        ssh_parts.extend([remote_host, f"kill -9 {remote_pid} 2>/dev/null; true"])

        try:
            subprocess.run(ssh_parts, capture_output=True, timeout=15)
        except Exception:
            pass  # best-effort

        self._repo.update_status(
            harness_run_id,
            status="cancelled",
            ended_at=time.time(),
        )

    def reconnect_and_reap(self, harness_run_id: str) -> bool:
        """Reconnect to remote host and kill orphaned process if still running.

        Returns True if process was found and killed, False otherwise.
        """
        info = self._runs.get(harness_run_id)
        if not info:
            return False

        remote_host = info["remote_host"]
        remote_pid = info["remote_pid"]
        ssh_user = info.get("ssh_user")
        ssh_key = info.get("ssh_key")

        ssh_parts = ["ssh", "-o", "ControlPath=/tmp/aw_ssh_%C"]
        if ssh_user:
            ssh_parts.extend(["-l", ssh_user])
        if ssh_key:
            ssh_parts.extend(["-i", ssh_key])

        # Check if process still exists
        check_cmd = ssh_parts + [remote_host, f"kill -0 {remote_pid} 2>/dev/null && echo ALIVE || echo DEAD"]
        try:
            result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=15)
            if "ALIVE" in result.stdout:
                # Kill it
                kill_cmd = ssh_parts + [remote_host, f"kill -9 {remote_pid} 2>/dev/null; true"]
                subprocess.run(kill_cmd, capture_output=True, timeout=15)
                self._repo.update_status(harness_run_id, status="cancelled", ended_at=time.time())
                return True
        except Exception:
            pass

        return False

    def get_runtime_ids(self, harness_run_id: str) -> RuntimeIds:
        info = self._runs.get(harness_run_id)
        if not info:
            return RuntimeIds()
        return RuntimeIds(
            session_id=harness_run_id,
            remote_host=info["remote_host"],
            remote_pid=info["remote_pid"],
        )

    def get_transcript(self, harness_run_id: str) -> Transcript:
        info = self._runs.get(harness_run_id)
        if not info:
            return Transcript()

        # Try to fetch remote log
        remote_host = info["remote_host"]
        ssh_user = info.get("ssh_user")
        ssh_key = info.get("ssh_key")
        ssh_parts = ["ssh", "-o", "ControlPath=/tmp/aw_ssh_%C"]
        if ssh_user:
            ssh_parts.extend(["-l", ssh_user])
        if ssh_key:
            ssh_parts.extend(["-i", ssh_key])

        try:
            result = subprocess.run(
                ssh_parts + [remote_host, f"cat /tmp/aw_run_{harness_run_id}.log 2>/dev/null || true"],
                capture_output=True, text=True, timeout=15,
            )
            return Transcript(stdout=result.stdout or info["stdout"], stderr=info["stderr"])
        except Exception:
            return Transcript(stdout=info["stdout"], stderr=info["stderr"])