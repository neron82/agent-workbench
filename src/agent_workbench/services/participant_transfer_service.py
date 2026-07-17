"""ParticipantTransferService — transfer participants between sessions."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from agent_workbench.models.participant_transfer import (
    ParticipantTransfer,
    ParticipantTransferRepository,
)
from agent_workbench.models.fork_record import ForkRecordRepository
from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.session_extension import SessionExtension, SessionExtensionRepository
from agent_workbench.services.participant_service import ParticipantService


class ParticipantTransferService:
    """Service for participant transfer operations between sessions.

    A new session can be created from an existing session while
    transferring selected/all participants and a compact context summary.
    Keeps the old fork records for history and compatibility; the
    user-facing action is ``Transfer to new session``.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.repo = ParticipantTransferRepository(conn)
        self.forks = ForkRecordRepository(conn)
        self.sessions = SessionExtensionRepository(conn)
        self.channels = ChannelRepository(conn)
        self.participants = ParticipantService(conn)

    def create_transfer(
        self,
        *,
        source_session_id: str,
        target_session_id: str,
        initiated_by: str = "user",
        participant_ids: Optional[List[str]] = None,
        context_summary: str = "",
    ) -> ParticipantTransfer:
        """Record a participant transfer operation.

        Parameters
        ----------
        source_session_id:
            The session to transfer participants from.
        target_session_id:
            The session to transfer participants to.
        initiated_by:
            Origin of the transfer request.
        participant_ids:
            Specific participant IDs to transfer. When ``None``, all
            active participants are transferred.
        context_summary:
            A compact summary of the source session context.

        Raises
        ------
        LookupError
            If the source or target session does not exist.
        """
        source = self.sessions.get_by_id(source_session_id)
        if source is None:
            raise LookupError(f"Source session not found: {source_session_id!r}")
        target = self.sessions.get_by_id(target_session_id)
        if target is None:
            raise LookupError(f"Target session not found: {target_session_id!r}")

        # Build the transferred participants payload
        transferred = []
        active = self.participants.list_active_participant_details(source_session_id)
        for detail in active:
            if participant_ids is None or detail["participant_id"] in participant_ids:
                transferred.append(
                    {
                        "participant_id": detail["participant_id"],
                        "agent_profile_id": detail["agent_profile_id"],
                        "agent_name": detail["agent_name"],
                        "role": detail["role"],
                        "binding_id": detail["binding_id"],
                    }
                )

        return self.repo.create(
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            initiated_by=initiated_by,
            transferred_participants=transferred,
            context_summary=context_summary,
        )

    def transfer_to_new_session(
        self,
        *,
        source_session_id: str,
        session_type: Optional[str] = None,
        title: Optional[str] = None,
        initiated_by: str = "user",
        participant_ids: Optional[List[str]] = None,
        context_summary: str = "",
    ) -> tuple[SessionExtension, ParticipantTransfer]:
        """Create a continuation session and copy selected participants into it.

        The source remains intact. Each copied participant gets a fresh binding
        scoped to the new session, while the transfer record preserves the
        source participant/binding IDs and summary for auditability.
        """
        source = self.sessions.get_by_id(source_session_id)
        if source is None:
            raise LookupError(f"Source session not found: {source_session_id!r}")
        selected = self._selected_participants(source_session_id, participant_ids)
        target = None
        channel = None
        fork = None
        try:
            # ``session_extensions.fork_id`` references fork_records.fork_id,
            # not the source session ID. Create the child first without the
            # optional link, then persist the real fork record and link it.
            target = self.sessions.create(
                workspace_id=source.workspace_id,
                session_type=session_type or source.session_type,
                status="active",
                title=title or ((source.title or source.session_type.title()) + " · continuation"),
                max_tool_iterations=source.max_tool_iterations,
                max_auto_turns=source.max_auto_turns,
            )
            channel = self.channels.create(
                workspace_id=target.workspace_id,
                channel_kind=target.session_type,
                title=target.title or "",
                active_session_id=target.session_id,
            )
            copied = []
            for detail in selected:
                participant = self.participants.add_participant(
                    session_id=target.session_id,
                    agent_profile_id=detail["agent_profile_id"],
                    participant_role=detail["role"],
                    added_by="system",
                )
                copied.append({
                    **detail,
                    "target_participant_id": participant.participant_id,
                })

            fork = self.forks.create(
                parent_session_id=source_session_id,
                child_session_id=target.session_id,
                fork_kind=(
                    "type_change"
                    if (session_type or source.session_type) != source.session_type
                    else "branch"
                ),
                fork_reason="Participant continuation",
                initiated_by=initiated_by,
                summary_ref=context_summary,
            )
            linked = self.sessions.update_fork_id(
                target.session_id, fork_id=fork.fork_id
            )
            if linked is None:
                raise LookupError(f"Target session not found: {target.session_id!r}")

            transfer = self.repo.create(
                source_session_id=source_session_id,
                target_session_id=target.session_id,
                initiated_by=initiated_by,
                transferred_participants=copied,
                context_summary=context_summary,
            )
            completed = self.repo.update_status(transfer.transfer_id, status="completed")
            if completed is None:
                raise LookupError(f"Transfer not found: {transfer.transfer_id!r}")
            return linked, completed
        except Exception:
            if fork is not None and target is not None:
                self.sessions.update_fork_id(target.session_id, fork_id=None)
                self.forks.delete(fork.fork_id)
            if channel is not None:
                self.channels.delete(channel.channel_id)
            if target is not None:
                self.sessions.delete(target.session_id)
            raise

    def _selected_participants(
        self, source_session_id: str, participant_ids: Optional[List[str]]
    ) -> List[Dict[str, Any]]:
        active = self.participants.list_active_participant_details(source_session_id)
        selected = [
            detail for detail in active
            if participant_ids is None or detail["participant_id"] in participant_ids
        ]
        if participant_ids is not None and len(selected) != len(set(participant_ids)):
            raise ValueError("One or more participant_ids are not active in the source session")
        return selected

    def get_transfer(self, transfer_id: str) -> ParticipantTransfer:
        transfer = self.repo.get_by_id(transfer_id)
        if transfer is None:
            raise LookupError(f"Transfer not found: {transfer_id!r}")
        return transfer

    def list_transfers_for_source(self, source_session_id: str) -> List[ParticipantTransfer]:
        return self.repo.list_by_source(source_session_id)

    def list_transfers_for_target(self, target_session_id: str) -> List[ParticipantTransfer]:
        return self.repo.list_by_target(target_session_id)

    def complete_transfer(self, transfer_id: str) -> ParticipantTransfer:
        """Mark a transfer as completed."""
        transfer = self.get_transfer(transfer_id)
        if transfer.status != "pending":
            raise ValueError(
                f"Cannot complete transfer {transfer_id!r}: "
                f"current status is {transfer.status!r}"
            )
        updated = self.repo.update_status(transfer_id, status="completed")
        if updated is None:
            raise LookupError(f"Transfer not found: {transfer_id!r}")
        return updated

    def fail_transfer(self, transfer_id: str) -> ParticipantTransfer:
        """Mark a transfer as failed."""
        transfer = self.get_transfer(transfer_id)
        if transfer.status != "pending":
            raise ValueError(
                f"Cannot fail transfer {transfer_id!r}: "
                f"current status is {transfer.status!r}"
            )
        updated = self.repo.update_status(transfer_id, status="failed")
        if updated is None:
            raise LookupError(f"Transfer not found: {transfer_id!r}")
        return updated

    def cancel_transfer(self, transfer_id: str) -> ParticipantTransfer:
        """Cancel a pending transfer."""
        transfer = self.get_transfer(transfer_id)
        if transfer.status != "pending":
            raise ValueError(
                f"Cannot cancel transfer {transfer_id!r}: "
                f"current status is {transfer.status!r}"
            )
        updated = self.repo.update_status(transfer_id, status="cancelled")
        if updated is None:
            raise LookupError(f"Transfer not found: {transfer_id!r}")
        return updated