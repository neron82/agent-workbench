"""Discussion harness adapter — no-process adapter for conversation-only runs."""

from __future__ import annotations

import time
from typing import Any, Optional

from agent_workbench.adapters.base import (
    AdapterCapabilities,
    BaseAdapter,
    HarnessNotReadyError,
    RuntimeIds,
    Transcript,
)
from agent_workbench.models.harness_run import HarnessRunRepository


class DiscussionAdapter(BaseAdapter):
    """Discussion adapter — records a HarnessRun but spawns no process.

    Capabilities: all False (discussion runs have no executable backend).
    Side-effect operations (shell, file write, replay, steer) are rejected.
    """

    adapter_type = "discussion"
    capabilities = AdapterCapabilities()  # all False

    def __init__(self, conn):
        super().__init__(conn)
        self._repo = HarnessRunRepository(conn)
        # {harness_run_id: session_id}
        self._sessions: dict[str, str] = {}

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
        """Create a HarnessRun record with harness_type='discussion'."""
        hr = self._repo.create(
            workspace_id=workspace_id,
            session_id=session_id,
            harness_type=self.adapter_type,
            task_spec_id=task_spec_id,
            status="running",
            control_capabilities=self.capabilities_dict(),
        )
        harness_run_id = hr.harness_run_id
        self._sessions[harness_run_id] = session_id
        self._repo.update_status(
            harness_run_id,
            status="running",
            started_at=time.time(),
        )
        return harness_run_id

    def stop(self, harness_run_id: str) -> None:
        """Mark the harness run as completed."""
        if harness_run_id not in self._sessions:
            raise HarnessNotReadyError(f"No discussion run for {harness_run_id}")
        self._repo.update_status(
            harness_run_id,
            status="completed",
            ended_at=time.time(),
        )

    def cancel(self, harness_run_id: str) -> None:
        """Mark the harness run as cancelled."""
        if harness_run_id not in self._sessions:
            raise HarnessNotReadyError(f"No discussion run for {harness_run_id}")
        self._repo.update_status(
            harness_run_id,
            status="cancelled",
            ended_at=time.time(),
        )

    # ------------------------------------------------------------------
    # Runtime info
    # ------------------------------------------------------------------

    def get_runtime_ids(self, harness_run_id: str) -> RuntimeIds:
        """Return RuntimeIds with only session_id set."""
        session_id = self._sessions.get(harness_run_id)
        return RuntimeIds(session_id=session_id)

    def get_transcript(self, harness_run_id: str) -> Transcript:
        """Return an empty transcript — discussion runs produce no stdout/stderr."""
        return Transcript()

    # ------------------------------------------------------------------
    # Side-effect rejection
    # ------------------------------------------------------------------

    def execute_shell(self, harness_run_id: str, command: str, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "DiscussionAdapter does not support shell execution"
        )

    def write_file(self, harness_run_id: str, path: str, data: str, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "DiscussionAdapter does not support file writes"
        )

    def replay(self, harness_run_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "DiscussionAdapter does not support replay"
        )

    def steer(self, harness_run_id: str, instruction: str, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "DiscussionAdapter does not support steering"
        )
