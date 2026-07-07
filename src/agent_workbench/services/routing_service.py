"""RoutingService — message routing, addressing, and anti-chatter enforcement.

Implements the routing model from ``07_EVENT_CHANNEL_MODEL.md`` and the
addressing/anti-chatter decisions in ``open_decisions.md`` (decisions 6, 7).

Core invariants enforced here:

* Every persisted message/event has a non-null source and target (decision 21,
  model rule §1).  The DB enforces this with ``NOT NULL`` constraints; the
  service also validates up front so callers get clear error messages before
  the INSERT fails.
* Default routing is ``user -> orchestrator -> worker`` (decision 6).  Direct
  worker dispatch is only allowed when explicitly requested via ``@agent``
  addressing or UI target selection.
* ``@all`` means broadcast to active non-execution discussion participants
  (decision 7).  Routing to an execution worker via ``@all`` is rejected.
* Workers do not address other workers directly (anti-chatter invariant,
  ``07_EVENT_CHANNEL_MODEL.md`` §6).  Inter-worker coordination is mediated
  by the orchestrator.

Supported addressing forms:

* ``@orchestrator`` — special target (type=``"orchestrator"``)
* ``@agent_name``   — explicit agent dispatch (type=``"agent"``)
* ``@all``          — broadcast to non-execution discussion participants
* ``@system``       — system bus (type=``"system"``)
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from agent_workbench.models.event_record import EventRecord, EventRecordRepository
from agent_workbench.models.routed_message import (
    RoutedMessage,
    RoutedMessageRepository,
)


# Recognised addressing forms.  These are the source/target ``type`` strings
# used in routed_messages.  ``"agent"`` covers both discussion agents and
# execution workers — the routing service decides which is which based on
# the channel and the explicit dispatch flag.
TARGET_TYPE_ORCHESTRATOR = "orchestrator"
TARGET_TYPE_AGENT = "agent"
TARGET_TYPE_ALL = "all"
TARGET_TYPE_SYSTEM = "system"

SOURCE_TYPE_USER = "user"
SOURCE_TYPE_ORCHESTRATOR = "orchestrator"
SOURCE_TYPE_AGENT = "agent"
SOURCE_TYPE_SYSTEM = "system"
SOURCE_TYPE_WORKER = "worker"

# ``worker`` is a valid source (workers emit reports) and a valid target
# (orchestrator dispatches to workers, UI may target a specific worker).
# The anti-chatter invariant in :meth:`RoutingService.route_message`
# forbids the ``worker -> worker`` hop specifically.
VALID_TARGET_TYPES = frozenset(
    {
        TARGET_TYPE_ORCHESTRATOR,
        TARGET_TYPE_AGENT,
        TARGET_TYPE_ALL,
        TARGET_TYPE_SYSTEM,
        SOURCE_TYPE_WORKER,
    }
)
VALID_SOURCE_TYPES = frozenset(
    {
        SOURCE_TYPE_USER,
        SOURCE_TYPE_ORCHESTRATOR,
        SOURCE_TYPE_AGENT,
        SOURCE_TYPE_SYSTEM,
        SOURCE_TYPE_WORKER,
    }
)

# message_kind values per 03_DOMAIN_MODEL.md §2 (RoutedMessage) and
# 07_EVENT_CHANNEL_MODEL.md §5.
VALID_MESSAGE_KINDS = frozenset(
    {
        "conversation", "dispatch", "steering", "report", "system", "telemetry",
        # New for tool-calling:
        "tool_confirmation_request",  # posted when a cross-harness call needs approval
        "tool_result",                 # posted when a tool call completes (or is denied)
        # Agent work steps (persistent "working" bubbles):
        "agent_work",
    }
)


class RoutingService:
    """Service layer for message routing and event recording.

    Wraps :class:`RoutedMessageRepository` and :class:`EventRecordRepository`
    and adds the addressing/anti-chatter invariants that are the policy
    layer above the raw persistence.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._messages = RoutedMessageRepository(conn)
        self._events = EventRecordRepository(conn)

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    def route_message(
        self,
        workspace_id: str,
        channel_id: str,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        message_kind: str,
        session_id: Optional[str] = None,
        payload_ref: Optional[str] = None,
        *,
        explicit_dispatch: bool = False,
    ) -> RoutedMessage:
        """Route a message and persist its routing metadata.

        Returns the persisted :class:`RoutedMessage`.  Raises
        :class:`ValueError` if any addressing invariant is violated.

        Parameters
        ----------
        workspace_id, channel_id:
            Identifiers of the workspace and channel the message belongs to.
        source_type, source_id:
            Who is sending the message.  Both must be non-null.
        target_type, target_id:
            Who the message is addressed to.  Both must be non-null.  Use
            ``target_id="@all"`` for broadcast (with ``target_type="all"``).
        message_kind:
            One of ``conversation | dispatch | steering | report | system |
            telemetry``.
        session_id, payload_ref:
            Optional linkage and payload reference.
        explicit_dispatch:
            Set to ``True`` when the call is the result of explicit ``@agent``
            addressing or explicit UI target selection.  Required to route
            a ``user -> worker`` or ``orchestrator -> worker`` message that
            is not the default ``user -> orchestrator`` step.
        """
        # ----- validate non-null addressing (better errors than DB) -----
        if not source_type:
            raise ValueError("source_type must be non-null")
        if not source_id:
            raise ValueError("source_id must be non-null")
        if not target_type:
            raise ValueError("target_type must be non-null")
        if not target_id:
            raise ValueError("target_id must be non-null")

        if source_type not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"Invalid source_type: {source_type!r}. "
                f"Must be one of {sorted(VALID_SOURCE_TYPES)}"
            )
        if target_type not in VALID_TARGET_TYPES:
            raise ValueError(
                f"Invalid target_type: {target_type!r}. "
                f"Must be one of {sorted(VALID_TARGET_TYPES)}"
            )
        if message_kind not in VALID_MESSAGE_KINDS:
            raise ValueError(
                f"Invalid message_kind: {message_kind!r}. "
                f"Must be one of {sorted(VALID_MESSAGE_KINDS)}"
            )

        # ----- addressing: @all targets non-execution discussion
        #       participants only (decision 7) -----
        if target_type == TARGET_TYPE_ALL and target_id != "@all":
            # Defensive: callers may pass target_id="all" or "@all" — normalise
            # to the canonical "@all" string.
            target_id = "@all"
        if target_type == TARGET_TYPE_ALL and source_type == SOURCE_TYPE_WORKER:
            raise ValueError(
                "Execution workers cannot broadcast via @all; "
                "@all targets active non-execution discussion participants "
                "only (decision 7)."
            )

        # ----- anti-chatter: workers do not address other workers
        #       directly (07_EVENT_CHANNEL_MODEL.md §6) -----
        # Implemented as a positive-list of legal worker outbound hops:
        # workers may ONLY address the orchestrator or the system bus.
        # Any other target (worker, agent, @all) would be either direct
        # chatter with another worker or a covert channel around it, and
        # is therefore rejected.  Inter-worker coordination is mediated
        # by the orchestrator.
        if source_type == SOURCE_TYPE_WORKER and target_type not in (
            TARGET_TYPE_ORCHESTRATOR,
            TARGET_TYPE_SYSTEM,
        ):
            raise ValueError(
                "Anti-chatter invariant: workers may only address the "
                "orchestrator or the system bus. Inter-worker coordination "
                "must be mediated by the orchestrator "
                "(07_EVENT_CHANNEL_MODEL.md §6)."
            )

        # ----- default routing: user -> orchestrator -----
        # If a user message targets a worker without an explicit dispatch
        # signal, that violates decision 6.  Direct worker dispatch is only
        # allowed via explicit @agent addressing or UI target selection.
        if (
            source_type == SOURCE_TYPE_USER
            and target_type == SOURCE_TYPE_WORKER
            and not explicit_dispatch
        ):
            raise ValueError(
                "Default routing is user -> orchestrator -> worker "
                "(decision 6). Direct worker dispatch requires explicit "
                "@agent addressing or explicit UI target selection."
            )

        return self._messages.create(
            workspace_id=workspace_id,
            channel_id=channel_id,
            session_id=session_id,
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            message_kind=message_kind,
            payload_ref=payload_ref,
        )

    def route_default_user_message(
        self,
        workspace_id: str,
        channel_id: str,
        user_id: str,
        message_kind: str = "conversation",
        session_id: Optional[str] = None,
        payload_ref: Optional[str] = None,
    ) -> RoutedMessage:
        """Route a user message along the default ``user -> orchestrator`` path.

        Convenience wrapper used by chat-style channels where the default
        routing (decision 6) applies.  Equivalent to calling
        :meth:`route_message` with ``source_type='user'`` and
        ``target_type='orchestrator'``.
        """
        return self.route_message(
            workspace_id=workspace_id,
            channel_id=channel_id,
            source_type=SOURCE_TYPE_USER,
            source_id=user_id,
            target_type=TARGET_TYPE_ORCHESTRATOR,
            target_id="@orchestrator",
            message_kind=message_kind,
            session_id=session_id,
            payload_ref=payload_ref,
        )

    def route_orchestrator_dispatch(
        self,
        workspace_id: str,
        channel_id: str,
        orchestrator_id: str,
        worker_id: str,
        message_kind: str = "dispatch",
        session_id: Optional[str] = None,
        payload_ref: Optional[str] = None,
    ) -> RoutedMessage:
        """Dispatch a worker from the orchestrator.

        This is the second hop of the default ``user -> orchestrator -> worker``
        path.  Orchestrator -> worker is always explicit (decision 6) and
        therefore allowed without an extra ``explicit_dispatch`` flag.
        """
        return self.route_message(
            workspace_id=workspace_id,
            channel_id=channel_id,
            source_type=SOURCE_TYPE_ORCHESTRATOR,
            source_id=orchestrator_id,
            target_type=SOURCE_TYPE_WORKER,
            target_id=worker_id,
            message_kind=message_kind,
            session_id=session_id,
            payload_ref=payload_ref,
            explicit_dispatch=True,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_messages_by_channel(self, channel_id: str) -> List[RoutedMessage]:
        """Return all routed messages for a channel, ordered by creation time."""
        return self._messages.list_by_channel(channel_id)

    def get_messages_by_session(self, session_id: str) -> List[RoutedMessage]:
        """Return all routed messages for a session, ordered by creation time."""
        return self._messages.list_by_session(session_id)

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def route_event(
        self,
        harness_run_id: Optional[str],
        event_type: str,
        event_source: str,
        routed_message_id: Optional[str] = None,
        payload_ref: Optional[str] = None,
    ) -> EventRecord:
        """Persist an EventRecord and return the persisted instance.

        ``event_type`` and ``event_source`` are required and must be non-null.
        ``harness_run_id``, ``routed_message_id`` and ``payload_ref`` are
        optional linkage fields.
        """
        if not event_type:
            raise ValueError("event_type must be non-null")
        if not event_source:
            raise ValueError("event_source must be non-null")

        return self._events.create(
            event_type=event_type,
            event_source=event_source,
            harness_run_id=harness_run_id,
            routed_message_id=routed_message_id,
            event_payload_ref=payload_ref,
        )

    def get_events_by_harness_run(
        self, harness_run_id: str
    ) -> List[EventRecord]:
        """Return all events for a harness run, ordered by timestamp."""
        return self._events.list_by_harness_run(harness_run_id)
