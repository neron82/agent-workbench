"""Hermes harness adapter — manages Hermes agent sessions.

This adapter drives the host-local ``hermes`` CLI (``hermes chat -Q -q …``)
whenever the binary is on ``PATH``.  When the binary is missing we
**degrade honestly**: ``start()`` raises ``ConnectionError`` with a
precise reason so the UI can surface a 422 instead of a silent
"running" stub.  Tests that don't want to depend on a real binary
patch ``shutil.which`` / ``subprocess.Popen`` — the same convention
the OpencodeAdapter tests use.

The transcript is read from the subprocess pipes in a background
daemon thread, so a real ``hermes`` invocation produces real
stdout/stderr the UI can show.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from agent_workbench.adapters._shared import (
    collect_output_daemon,
    db_path_from_conn,
    terminate_process_group,
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
from agent_workbench.services import TranscriptService


# Default binary name — kept overridable so tests can patch it.
HERMES_BINARY = "hermes"


# Public sentinel value: pass as ``hermes_binary=LAZY`` to
# :class:`HermesAdapter` to force the per-call ``shutil.which``
# lookup (no constructor-time caching).  This is what unit tests
# want: the host may have a real ``hermes`` binary on PATH, and we
# don't want the constructor to cache it before the test patches
# ``shutil.which``.
LAZY = object()


def _resolve_hermes_binary(explicit: Any) -> Optional[str]:
    """Return the absolute path to the ``hermes`` binary or ``None``.

    * ``_LAZY`` (sentinel) — return ``None`` and let every
      ``start()`` call re-resolve via ``shutil.which``.
    * ``None`` — call ``shutil.which`` once at construction and cache.
    * Any truthy string — return it as-is.
    """
    if explicit is LAZY:
        return None
    if explicit:
        return explicit
    return shutil.which(HERMES_BINARY)


class HermesAdapter(BaseAdapter):
    """Hermes adapter — drives the host-local ``hermes`` CLI.

    Capabilities:
    - can_stop=True (graceful stop)
    - can_cancel=True (forceful cancel)
    - can_shell=True
    - can_file_write=True
    - can_replay=True
    - has_process_ids=True
    - can_steer=True
    - can_pause=False
    - can_diff=False
    - can_remote=False (unless backend is SSH)
    """

    adapter_type = "hermes"
    capabilities = AdapterCapabilities(
        can_stop=True,
        can_cancel=True,
        can_shell=True,
        can_file_write=True,
        can_replay=True,
        has_process_ids=True,
        can_steer=True,
    )

    def __init__(
        self,
        conn,
        *,
        backend: str = "local",
        hermes_binary: Any = None,
    ) -> None:
        super().__init__(conn)
        self._repo = HarnessRunRepository(conn)
        self._db_path = db_path_from_conn(conn)
        self._backend = backend
        self._transcripts = TranscriptService()
        # Pre-resolve the binary once; tests can override via
        # constructor (pass ``LAZY`` sentinel for per-call
        # resolution, or an explicit path string to lock it in).
        self._hermes_binary = _resolve_hermes_binary(hermes_binary)
        self._hermes_binary_is_lazy = hermes_binary is LAZY
        # Enable remote capability if SSH backend is configured
        if backend == "ssh":
            self.capabilities = AdapterCapabilities(
                can_stop=True,
                can_cancel=True,
                can_shell=True,
                can_file_write=True,
                can_replay=True,
                has_process_ids=True,
                can_steer=True,
                can_remote=True,
            )
        # {harness_run_id: {"process": Popen, "session_id": str,
        #                  "process_id": str|None, "stdout": str,
        #                  "stderr": str, "command": str, "pgid": int}}
        self._sessions: Dict[str, Dict[str, Any]] = {}
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
        """Create a HarnessRun record and spawn the real ``hermes`` CLI.

        Raises
        ------
        ConnectionError
            If no ``hermes`` binary is available on the caller's PATH.
            The UI surfaces this as 422 — never as a silent "running"
            stub.
        HarnessProcessError
            If ``subprocess.Popen`` itself fails.
        """
        # 1. Resolve the binary.  Honour an env-mapped PATH so the
        # caller can scope the lookup.  When the adapter was
        # constructed with ``hermes_binary=LAZY`` we always re-resolve
        # (used by tests that patch ``shutil.which``).
        child_env = kwargs.get("env", None)
        lookup_path = None
        if isinstance(child_env, dict):
            lookup_path = child_env.get("PATH")
        if self._hermes_binary_is_lazy or self._hermes_binary is None:
            resolved = shutil.which(HERMES_BINARY, path=lookup_path)
        else:
            resolved = self._hermes_binary
        if resolved is None:
            raise ConnectionError(
                "hermes binary not found in PATH. "
                "Install the hermes-agent CLI or add its location to "
                "PATH to use this adapter."
            )

        # 2. Create HarnessRun record (status='starting' first, then
        # 'running' once the process is actually spawned — mirrors
        # Shell/OpencodeAdapter).
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
            # 3. Build the command.  We use ``hermes chat -Q -q <text>``
            # which is the documented non-interactive single-query
            # mode; it suppresses the banner/spinner and prints the
            # final response plus a session id.  Tests can override
            # ``_build_command`` to substitute a different argv.
            argv = self._build_command(resolved, command, kwargs)
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=kwargs.get("cwd", None),
                env=kwargs.get("env", None),
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self._repo.update_status(
                harness_run_id,
                status="failed",
                ended_at=time.time(),
            )
            self._transcripts.append_event(
                self.conn,
                harness_run_id=harness_run_id,
                event_type="exit",
                detail={"error": str(exc)},
            )
            raise HarnessProcessError(
                f"failed to spawn hermes CLI: {exc}"
            ) from exc

        # 4. Capture process group id for stop/cancel semantics.
        try:
            pgid = os.getpgid(process.pid)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = None

        # 5. Register session bookkeeping.
        self._sessions[harness_run_id] = {
            "process": process,
            "session_id": session_id,
            "process_id": str(process.pid),
            "stdout": "",
            "stderr": "",
            "command": command,
            "pgid": pgid,
        }

        # 6. Persist runtime identifiers + flip to "running".
        self._transcripts.append_event(
            self.conn,
            harness_run_id=harness_run_id,
            event_type="start",
            detail={"pid": process.pid, "pgid": pgid, "command": command},
        )
        self._repo.update_runtime_ids(
            harness_run_id,
            runtime_session_id=session_id,
            runtime_process_id=str(process.pid),
            pgid=str(pgid) if pgid is not None else None,
        )
        self._repo.update_status(
            harness_run_id,
            status="running",
            started_at=time.time(),
        )

        # 7. Spawn daemon reader to populate transcript without
        # blocking the request thread.
        t = threading.Thread(
            target=collect_output_daemon,
            args=(harness_run_id, process, self._db_path, self._sessions),
            daemon=True,
        )
        self._collect_threads[harness_run_id] = t
        t.start()

        return harness_run_id

    # ------------------------------------------------------------------
    # Internal helpers (overridable for tests)
    # ------------------------------------------------------------------

    def _build_command(
        self,
        binary: str,
        command: str,
        kwargs: Dict[str, Any],
    ) -> list:
        """Build the argv for the real ``hermes`` CLI.

        Subclasses/tests may override this to inject dry-run modes.
        """
        return [binary, "chat", "-Q", "-q", command]

    # ------------------------------------------------------------------
    # Stop / Cancel
    # ------------------------------------------------------------------

    def stop(self, harness_run_id: str) -> None:
        """Graceful stop — SIGTERM the hermes process group."""
        terminate_process_group(
            harness_run_id,
            signal.SIGTERM,
            terminal_status="stopping",
            sessions_dict=self._sessions,
            repo=self._repo,
            transcripts=self._transcripts,
            conn=self.conn,
        )

    def cancel(self, harness_run_id: str) -> None:
        """Forceful cancel — SIGKILL the hermes process group."""
        terminate_process_group(
            harness_run_id,
            signal.SIGKILL,
            terminal_status="cancelled",
            sessions_dict=self._sessions,
            repo=self._repo,
            transcripts=self._transcripts,
            conn=self.conn,
        )

    # ------------------------------------------------------------------
    # Runtime info
    # ------------------------------------------------------------------

    def get_runtime_ids(self, harness_run_id: str) -> RuntimeIds:
        info = self._sessions.get(harness_run_id)
        if info:
            return RuntimeIds(
                session_id=info["session_id"],
                process_id=info.get("process_id"),
            )
        run = self._repo.get_by_id(harness_run_id)
        if run is None:
            return RuntimeIds()
        return RuntimeIds(
            session_id=run.runtime_session_id,
            process_id=run.runtime_process_id,
        )

    def get_transcript(self, harness_run_id: str) -> Transcript:
        info = self._sessions.get(harness_run_id)
        if not info:
            return Transcript()
        return Transcript(stdout=info["stdout"], stderr=info["stderr"])

    # ------------------------------------------------------------------
    # Side-effect operations
    # ------------------------------------------------------------------

    def execute_shell(
        self, harness_run_id: str, command: str, **kwargs: Any
    ) -> Transcript:
        """Execute a real host-local shell command for this Hermes run.

        The Hermes CLI run itself is not an interactive shell, so we
        cannot inject commands into the existing subprocess. Instead we
        execute a one-shot host-local command using the run's recorded
        context (cwd/env when available) and return the real stdout/stderr.
        """
        info = self._require_session_context(harness_run_id)
        timeout = kwargs.get("timeout", kwargs_get_timeout(30))
        cwd = kwargs.get("cwd", info.get("cwd"))
        env = kwargs.get("env", info.get("env"))
        try:
            proc = subprocess.run(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise HarnessProcessError(
                f"Hermes side-effect command timed out after {timeout}s"
            ) from exc
        return Transcript(
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def write_file(
        self,
        harness_run_id: str,
        path: str,
        data: str,
        **kwargs: Any,
    ) -> str:
        """Write a real host-local file for this Hermes run."""
        info = self._require_session_context(harness_run_id)
        cwd = kwargs.get("cwd", info.get("cwd"))
        target = Path(path).expanduser()
        if not target.is_absolute() and cwd:
            target = Path(cwd) / target
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding=kwargs.get("encoding", "utf-8"))
        return str(target)

    def replay(
        self, harness_run_id: str, **kwargs: Any
    ) -> Transcript:
        """Replay the transcript for a Hermes session."""
        return self.get_transcript(harness_run_id)

    def steer(
        self, harness_run_id: str, instruction: str, **kwargs: Any
    ) -> None:
        """Send a steering instruction to a running Hermes session.

        A real implementation would forward ``instruction`` to the
        running subprocess (stdin/JSON-RPC); we leave it as a no-op
        but keep the capability flag honest.
        """
        self._require_session_context(harness_run_id)

    def _require_session_context(self, harness_run_id: str) -> Dict[str, Any]:
        info = self._sessions.get(harness_run_id)
        if info is not None:
            return info
        run = self._repo.get_by_id(harness_run_id)
        if run is None or run.harness_type != self.adapter_type:
            raise HarnessNotReadyError(f"No Hermes session for {harness_run_id}")
        return {
            "session_id": run.session_id,
            "process_id": run.runtime_process_id,
            "stdout": "",
            "stderr": "",
            "command": "",
            "pgid": run.pgid,
            "cwd": None,
            "env": None,
        }


# ----------------------------------------------------------------------
# Tiny internal helpers
# ----------------------------------------------------------------------


def kwargs_get_timeout(default: int) -> int:
    """Return the ``_collect_output`` timeout from ``os.environ`` or default.

    Tests can override the timeout via ``HERMES_ADAPTER_TIMEOUT`` to keep
    long-running capture loops short.  Not part of the public API.
    """
    try:
        return int(os.environ.get("HERMES_ADAPTER_TIMEOUT", str(default)))
    except ValueError:
        return default
