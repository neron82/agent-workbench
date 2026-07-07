"""SessionService — session lifecycle, status, and type transitions.

Wraps ``SessionExtensionRepository`` with business logic:

- session creation (with optional channel linking)
- status updates
- task-spec assignment
- type transitions (which always delegate to ForkService — a session
  type is immutable in place; the new type lives on a forked child)
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import List, Optional, Tuple

from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.fork_record import ForkRecord
from agent_workbench.models.session_extension import (
    SESSION_STATUSES,
    SessionExtension,
    SessionExtensionRepository,
)
from agent_workbench.models.task_spec import TaskSpecRepository
from agent_workbench.services.fork_service import ForkService


class SessionNotFoundError(LookupError):
    """Raised when a session cannot be found."""


# ── Default max_tool_iterations by session type ────────────────────────
DEFAULT_MAX_TOOL_ITERATIONS: dict[str, int] = {
    "chat": 5,
    "research": 10,
    "work": 25,
}

# ── Default max_auto_turns by session type ──────────────────────────────
DEFAULT_MAX_AUTO_TURNS: dict[str, int] = {
    "chat": 0,      # off — simple broadcast
    "research": 3,  # allow a few back-and-forth turns
    "work": 5,      # longer chains for structured work
}


class SessionService:
    """High-level session lifecycle service."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.sessions = SessionExtensionRepository(conn)
        self.channels = ChannelRepository(conn)
        self.forks = ForkService(conn)
        self._task_specs = TaskSpecRepository(conn)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_session(
        self,
        workspace_id: str,
        session_type: str,
        channel_id: Optional[str] = None,
        title: Optional[str] = None,
        max_tool_iterations: Optional[int] = None,
        max_auto_turns: Optional[int] = None,
    ) -> SessionExtension:
        """Create a new session and (optionally) link it as the channel's
        active session.

        If ``max_tool_iterations`` is not provided, the default for the
        session type is applied (chat=5, research=10, work=25).
        If ``max_auto_turns`` is not provided, the default for the
        session type is applied (chat=0, research=3, work=5).
        """
        if max_tool_iterations is None:
            max_tool_iterations = DEFAULT_MAX_TOOL_ITERATIONS.get(session_type, 5)
        if max_auto_turns is None:
            max_auto_turns = DEFAULT_MAX_AUTO_TURNS.get(session_type, 0)
        session = self.sessions.create(
            workspace_id=workspace_id,
            session_type=session_type,
            title=title,
            max_tool_iterations=max_tool_iterations,
            max_auto_turns=max_auto_turns,
        )

        if channel_id is not None:
            channel = self.channels.get_by_id(channel_id)
            if channel is None:
                raise SessionNotFoundError(f"Channel not found: {channel_id!r}")
            if channel.workspace_id != workspace_id:
                raise ValueError(
                    f"Channel {channel_id!r} belongs to workspace "
                    f"{channel.workspace_id!r}, not {workspace_id!r}"
                )
            self.channels.update_active_session(
                channel_id, active_session_id=session.session_id
            )

        return session

    def get_session(self, session_id: str) -> SessionExtension:
        """Return the session or raise :class:`SessionNotFoundError`."""
        session = self.sessions.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(f"Session not found: {session_id!r}")
        return session

    def list_sessions(self, workspace_id: str) -> List[SessionExtension]:
        return self.sessions.list_by_workspace(workspace_id)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def update_session_status(
        self, session_id: str, status: str
    ) -> SessionExtension:
        """Update the status of a session and return the refreshed row."""
        if status not in SESSION_STATUSES:
            raise ValueError(
                f"Invalid status: {status!r}. Must be one of {SESSION_STATUSES}"
            )
        updated = self.sessions.update_status(session_id, status=status)
        if updated is None:
            raise SessionNotFoundError(f"Session not found: {session_id!r}")
        return updated

    def update_session_title(
        self, session_id: str, title: Optional[str]
    ) -> SessionExtension:
        """Update the title of a session."""
        updated = self.sessions.update_title(session_id, title=title)
        if updated is None:
            raise SessionNotFoundError(f"Session not found: {session_id!r}")
        return updated

    def update_session_max_tool_iterations(
        self, session_id: str, max_tool_iterations: int
    ) -> SessionExtension:
        """Update the max_tool_iterations of a session."""
        if max_tool_iterations < 1:
            raise ValueError("max_tool_iterations must be at least 1")
        updated = self.sessions.update_max_tool_iterations(
            session_id, max_tool_iterations=max_tool_iterations
        )
        if updated is None:
            raise SessionNotFoundError(f"Session not found: {session_id!r}")
        return updated

    def update_session_max_auto_turns(
        self, session_id: str, max_auto_turns: int
    ) -> SessionExtension:
        """Update the max_auto_turns of a session."""
        if max_auto_turns < 0:
            raise ValueError("max_auto_turns must be >= 0")
        updated = self.sessions.update_max_auto_turns(
            session_id, max_auto_turns=max_auto_turns
        )
        if updated is None:
            raise SessionNotFoundError(f"Session not found: {session_id!r}")
        return updated

    def delete_session(self, session_id: str) -> None:
        """Delete a session and all associated data (cascade).

        Order is critical: child rows must be deleted before parent rows
        to satisfy FK constraints.  Row-based deletes for tables that
        reference the session directly, plus intermediate cleanup for
        tables that reference indirect children (e.g. harness_events
        references harness_runs).
        """
        session = self.get_session(session_id)
        ws_id = session.workspace_id

        # 1. Unlink from channel if linked
        self.conn.execute(
            "UPDATE channels SET active_session_id = NULL "
            "WHERE active_session_id = ?",
            (session_id,),
        )

        # 2. Collect child IDs we need for intermediate FK cleanup
        hrun_ids = [
            r["harness_run_id"]
            for r in self.conn.execute(
                "SELECT harness_run_id FROM harness_runs WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        ]
        fk_ids = [
            r["fork_id"]
            for r in self.conn.execute(
                "SELECT fork_id FROM fork_records WHERE parent_session_id = ? OR child_session_id = ?",
                (session_id, session_id),
            ).fetchall()
        ]
        binding_ids = [
            r["binding_id"]
            for r in self.conn.execute(
                "SELECT binding_id FROM agent_profile_bindings WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        ]

        # 3. Delete harness_children (NOT NULL FKs to harness_runs)
        for hrid in hrun_ids:
            self.conn.execute(
                "DELETE FROM harness_events WHERE harness_run_id = ?", (hrid,)
            )
            self.conn.execute(
                "DELETE FROM harness_transcripts WHERE harness_run_id = ?", (hrid,)
            )
            self.conn.execute(
                "DELETE FROM permission_requests WHERE harness_run_id = ?", (hrid,)
            )

        # 4. NULL optional FKs (event_records, artifacts)
        for hrid in hrun_ids:
            self.conn.execute(
                "UPDATE event_records SET harness_run_id = NULL WHERE harness_run_id = ?",
                (hrid,),
            )
            self.conn.execute(
                "UPDATE artifacts SET producer_harness_run_id = NULL WHERE producer_harness_run_id = ?",
                (hrid,),
            )
            self.conn.execute(
                "UPDATE replay_records SET source_harness_run_id = NULL WHERE source_harness_run_id = ?",
                (hrid,),
            )

        # 5. Delete harness runs
        self.conn.execute(
            "DELETE FROM harness_runs WHERE session_id = ?",
            (session_id,),
        )

        # 6. Delete replay_records (NOT NULL FK to fork_records)
        for fid in fk_ids:
            self.conn.execute(
                "DELETE FROM replay_records WHERE fork_id = ?", (fid,)
            )

        # 7. NULL fork_id on child sessions
        for fid in fk_ids:
            self.conn.execute(
                "UPDATE session_extensions SET fork_id = NULL WHERE fork_id = ?", (fid,)
            )

        # 8. Delete fork records
        self.conn.execute(
            "DELETE FROM fork_records WHERE parent_session_id = ? OR child_session_id = ?",
            (session_id, session_id),
        )

        # 9. Delete tool invocations
        self.conn.execute(
            "DELETE FROM tool_invocations WHERE session_id = ?",
            (session_id,),
        )

        # 10. NULL review_records FK (optional FK to bindings)
        for bid in binding_ids:
            self.conn.execute(
                "UPDATE review_records SET reviewer_binding_id = NULL WHERE reviewer_binding_id = ?",
                (bid,),
            )

        # 11. NULL session_extensions FK to bindings
        self.conn.execute(
            "UPDATE session_extensions SET agent_profile_binding_id = NULL WHERE session_id = ?",
            (session_id,),
        )

        # 12. Delete bindings (hard-delete participants first — NOT NULL FK)
        self.conn.execute(
            "DELETE FROM session_participants WHERE session_id = ?",
            (session_id,),
        )
        self.conn.execute(
            "DELETE FROM agent_profile_bindings WHERE session_id = ?",
            (session_id,),
        )

        # 13. Delete routed messages
        self.conn.execute(
            "DELETE FROM routed_messages WHERE session_id = ?",
            (session_id,),
        )

        # 14. Delete the session itself
        self.sessions.delete(session_id)
        self.conn.commit()

    def assign_task_spec(
        self, session_id: str, task_spec_id: Optional[str]
    ) -> SessionExtension:
        """Attach (or clear) the task_spec_id on a session.

        Workspace/tenant isolation: when ``task_spec_id`` is provided,
        the referenced :class:`TaskSpec` must live in the same
        workspace as the session. Cross-workspace assignment would
        let a user in workspace A attach a spec from workspace B (or
        vice versa), which violates the per-tenant invariant defined
        in 03_DOMAIN_MODEL.md §1 ("every session, run, board, and
        artifact belongs to a workspace/tenant context"). Passing
        ``task_spec_id=None`` is always allowed and clears the
        linkage.
        """
        session = self.get_session(session_id)
        if task_spec_id is not None:
            spec = self._task_specs.get_by_id(task_spec_id)
            if spec is None:
                raise LookupError(
                    f"TaskSpec {task_spec_id!r} not found"
                )
            if spec.workspace_id != session.workspace_id:
                raise ValueError(
                    f"TaskSpec {task_spec_id!r} belongs to workspace "
                    f"{spec.workspace_id!r}, not {session.workspace_id!r}; "
                    f"cross-workspace assignment is not allowed."
                )
        updated = self.sessions.update_task_spec(
            session_id, task_spec_id=task_spec_id
        )
        if updated is None:
            raise SessionNotFoundError(f"Session not found: {session_id!r}")
        return updated

    # ------------------------------------------------------------------
    # Type transitions — always via fork
    # ------------------------------------------------------------------

    def transition_session_type(
        self,
        session_id: str,
        new_type: str,
        fork_reason: str,
        initiated_by: str = "orchestrator",
    ) -> Tuple[SessionExtension, ForkRecord]:
        """Transition a session to a new ``session_type`` via structured fork.

        The product rule is that ``session_type`` is immutable in place;
        a type change always creates a fork record and a new child session.
        This method delegates to :class:`ForkService.create_fork`, providing
        the child ``session_id`` and a non-empty summary it requires.
        """
        parent = self.get_session(session_id)

        # Pre-allocate a child session id so we can pass it to ForkService.
        child_session_id = uuid.uuid4().hex

        # The fork service requires a non-empty summary. We supply a
        # minimal, machine-generated bootstrap summary that downstream
        # consolidators can replace. The presence of any non-empty value
        # satisfies the spec's "summary must not be empty" rule.
        default_summary = (
            f"Forked from session {parent.session_id} "
            f"({parent.session_type} -> {new_type}) at "
            f"{time.time():.0f}. Reason: {fork_reason or 'unspecified'}. "
            f"Initiated by: {initiated_by}."
        )

        fork = self.forks.create_fork(
            parent_session_id=parent.session_id,
            child_session_id=child_session_id,
            new_session_type=new_type,
            fork_reason=fork_reason,
            initiated_by=initiated_by,
            summary=default_summary,
            decisions={"transition": new_type, "from": parent.session_type},
        )

        # Re-read the freshly-created child session (the sibling ForkService
        # creates the child row inside the same call).
        child = self.sessions.get_by_id(child_session_id)
        if child is None:
            # Defensive: should be impossible after a successful create_fork.
            raise RuntimeError(
                f"ForkService.create_fork reported success but child "
                f"session {child_session_id!r} is missing."
            )

        # Inherit the parent's task_spec_id (the spec carries forward
        # structured context on forks). This is a small follow-up write;
        # if it fails the child + fork are still consistent, we just
        # surface the error.
        if parent.task_spec_id is not None:
            inherited = self.sessions.update_task_spec(
                child_session_id, task_spec_id=parent.task_spec_id
            )
            if inherited is not None:
                child = inherited
        return child, fork
