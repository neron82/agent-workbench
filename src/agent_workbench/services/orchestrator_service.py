"""OrchestratorService — top-level orchestration surface.

The orchestrator is the only default scope owner. It:

- dispatches workers by binding an AgentProfile to a session
- mediates all inter-worker communication (workers cannot talk directly;
  every cross-worker message is written as two routed-message legs
  through the orchestrator)
- manages channel CRUD as a thin convenience surface
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

from agent_workbench.models.agent_profile_binding import (
    AgentProfileBinding,
    AgentProfileBindingRepository,
)
from agent_workbench.models.channel import (
    CHANNEL_KINDS,
    Channel,
    ChannelRepository,
)
from agent_workbench.models.routed_message import (
    RoutedMessage,
    RoutedMessageRepository,
)
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.session_service import SessionService


# Source/target type taxonomy used by RoutedMessage rows.
SOURCE_TARGET_ORCHESTRATOR = "orchestrator"
SOURCE_TARGET_WORKER = "worker"

MESSAGE_KIND_DISPATCH = "dispatch"
MESSAGE_KIND_CONVERSATION = "conversation"
MESSAGE_KIND_STEERING = "steering"
MESSAGE_KIND_REPORT = "report"
MESSAGE_KIND_SYSTEM = "system"
MESSAGE_KIND_TELEMETRY = "telemetry"


class ChannelNotFoundError(LookupError):
    """Raised when a channel cannot be found."""


class OrchestratorService:
    """Top-level orchestration service."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.channels = ChannelRepository(conn)
        self.bindings = AgentProfileBindingRepository(conn)
        self.messages = RoutedMessageRepository(conn)
        self.sessions = SessionExtensionRepository(conn)
        self._profiles = ProfileService(conn)
        self._session_service = SessionService(conn)

    # ------------------------------------------------------------------
    # Worker dispatch
    # ------------------------------------------------------------------

    def dispatch_worker(
        self,
        session_id: str,
        agent_profile_id: str,
        task_spec_id: Optional[str] = None,
    ) -> AgentProfileBinding:
        """Bind an AgentProfile to a session for work.

        Optionally assigns a task_spec to the session. Returns the new
        ``AgentProfileBinding`` that represents the worker.
        """
        binding = self._profiles.bind_profile(
            session_id=session_id,
            agent_profile_id=agent_profile_id,
            created_from="initial",
        )

        if task_spec_id is not None:
            # Use the repository directly so we don't depend on session_service
            # raising on None task_spec (it allows None = clear).
            updated = self.sessions.update_task_spec(
                session_id, task_spec_id=task_spec_id
            )
            if updated is None:
                raise LookupError(f"Session vanished mid-dispatch: {session_id!r}")

        return binding

    # ------------------------------------------------------------------
    # Worker communication mediation
    # ------------------------------------------------------------------

    def mediate_worker_communication(
        self,
        source_worker_id: str,
        target_worker_id: str,
        message: Dict[str, Any],
    ) -> RoutedMessage:
        """Route a message from one worker to another via the orchestrator.

        Workers cannot talk directly. This method writes two legs:

        1. ``source_worker -> orchestrator`` (uplink)
        2. ``orchestrator -> target_worker`` (downlink)

        The returned ``RoutedMessage`` is the downlink — the one that
        actually reaches the target worker. The uplink row is also
        persisted (in the same transaction) and discoverable via
        ``RoutedMessageRepository.list_by_target('orchestrator', ...)``.
        """
        if source_worker_id == target_worker_id:
            raise ValueError(
                "Worker cannot send a mediated message to itself; "
                "use direct channel emit instead."
            )
        if not isinstance(message, dict):
            raise TypeError(
                f"message must be a dict, got {type(message).__name__}"
            )

        workspace_id = self._resolve_worker_workspace_id(source_worker_id)

        # We need a channel for the routed_messages.channel_id NOT NULL
        # constraint. The worker's session's channel is the natural choice.
        channel_id = self._resolve_worker_channel_id(source_worker_id)

        # Serialize the message as the payload_ref so it is recoverable
        # through the same envelope pattern the rest of the system uses.
        payload_ref = self._serialize_payload(message)

        # 1) Uplink leg: source_worker -> orchestrator
        self.messages.create(
            workspace_id=workspace_id,
            channel_id=channel_id,
            session_id=None,
            source_type=SOURCE_TARGET_WORKER,
            source_id=source_worker_id,
            target_type=SOURCE_TARGET_ORCHESTRATOR,
            target_id="orchestrator",
            message_kind=MESSAGE_KIND_CONVERSATION,
            payload_ref=payload_ref,
        )

        # 2) Downlink leg: orchestrator -> target_worker
        downlink = self.messages.create(
            workspace_id=workspace_id,
            channel_id=channel_id,
            session_id=None,
            source_type=SOURCE_TARGET_ORCHESTRATOR,
            source_id="orchestrator",
            target_type=SOURCE_TARGET_WORKER,
            target_id=target_worker_id,
            message_kind=MESSAGE_KIND_CONVERSATION,
            payload_ref=payload_ref,
        )

        return downlink

    # ------------------------------------------------------------------
    # Channel management
    # ------------------------------------------------------------------

    def create_channel(
        self,
        workspace_id: str,
        channel_kind: str,
        title: str = "",
        default_target: Optional[str] = None,
        status: str = "active",
    ) -> Channel:
        if channel_kind not in CHANNEL_KINDS:
            raise ValueError(
                f"Invalid channel_kind: {channel_kind!r}. "
                f"Must be one of {CHANNEL_KINDS}"
            )
        return self.channels.create(
            workspace_id=workspace_id,
            channel_kind=channel_kind,
            title=title,
            default_target=default_target,
            status=status,
        )

    def get_channel(self, channel_id: str) -> Channel:
        ch = self.channels.get_by_id(channel_id)
        if ch is None:
            raise ChannelNotFoundError(f"Channel not found: {channel_id!r}")
        return ch

    def list_channels(self, workspace_id: str) -> List[Channel]:
        return self.channels.list_by_workspace(workspace_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_worker_workspace_id(self, worker_id: str) -> str:
        """Look up a worker's session -> workspace_id.

        A "worker" identity in MVP is an ``AgentProfileBinding``. The
        session that owns the binding carries the workspace_id.
        """
        binding = self.bindings.get_by_id(worker_id)
        if binding is None:
            raise LookupError(f"Worker (binding) not found: {worker_id!r}")
        session = self.sessions.get_by_id(binding.session_id)
        if session is None:
            raise LookupError(
                f"Worker {worker_id!r} has no backing session: "
                f"{binding.session_id!r}"
            )
        return session.workspace_id

    def _resolve_worker_channel_id(self, worker_id: str) -> str:
        """Find a non-empty channel_id for routed-message rows.

        Routed messages require a non-null channel_id. If the worker's
        session is not yet linked to any channel, we fall back to a
        synthetic "system" channel in the same workspace so the mediation
        envelope can still be persisted.
        """
        binding = self.bindings.get_by_id(worker_id)
        assert binding is not None  # already checked by _resolve_worker_workspace_id
        session = self.sessions.get_by_id(binding.session_id)
        assert session is not None

        workspace_channels = self.channels.list_by_workspace(session.workspace_id)
        for ch in workspace_channels:
            if ch.active_session_id == session.session_id:
                return ch.channel_id
        # Fallback: first channel in the workspace, or synthesize a system one.
        if workspace_channels:
            return workspace_channels[0].channel_id
        system_channel = self.create_channel(
            workspace_id=session.workspace_id,
            channel_kind="system",
            title="orchestrator-internal",
        )
        return system_channel.channel_id

    @staticmethod
    def _serialize_payload(message: Dict[str, Any]) -> str:
        """Persist the mediated message as a JSON payload_ref."""
        envelope = {
            "envelope": "orchestrator_mediated",
            "envelope_ts": time.time(),
            "message": message,
        }
        return json.dumps(envelope, sort_keys=True, default=str)
