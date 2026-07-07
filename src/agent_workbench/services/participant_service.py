"""ParticipantService — add/remove/list session participants."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from agent_workbench.models.agent_profile import AgentProfileRepository
from agent_workbench.models.agent_profile_binding import AgentProfileBindingRepository
from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.role import RoleRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.session_participant import (
    SessionParticipant,
    SessionParticipantRepository,
)
from agent_workbench.services.profile_service import ProfileService, ProfileNotFoundError


class ParticipantNotFoundError(LookupError):
    """Raised when a participant cannot be found."""


class ParticipantService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.participants = SessionParticipantRepository(conn)
        self.sessions = SessionExtensionRepository(conn)
        self.bindings = AgentProfileBindingRepository(conn)
        self.profiles = AgentProfileRepository(conn)
        self.roles = RoleRepository(conn)
        self.channels = ChannelRepository(conn)
        self.profile_service = ProfileService(conn)

    def add_participant(
        self,
        *,
        session_id: str,
        agent_profile_id: str,
        participant_role: str = "member",
        added_by: str = "user",
    ) -> SessionParticipant:
        session = self.sessions.get_by_id(session_id)
        if session is None:
            raise ParticipantNotFoundError(f"Session not found: {session_id!r}")
        if self.profiles.get_by_id(agent_profile_id) is None:
            raise ProfileNotFoundError(f"AgentProfile not found: {agent_profile_id!r}")

        for detail in self.list_active_participant_details(session_id):
            if detail["agent_profile_id"] == agent_profile_id:
                participant = self.participants.get_by_id(detail["participant_id"])
                assert participant is not None
                return participant

        existing_history = self.conn.execute(
            "SELECT sp.participant_id FROM session_participants sp "
            "JOIN agent_profile_bindings apb ON apb.binding_id = sp.binding_id "
            "WHERE sp.session_id = ? AND apb.agent_profile_id = ? "
            "ORDER BY sp.added_at DESC LIMIT 1",
            (session_id, agent_profile_id),
        ).fetchone()
        if existing_history is not None:
            reactivated = self.participants.reactivate(existing_history["participant_id"])
            if reactivated is not None:
                return reactivated

        binding = self.profile_service.bind_profile(
            session_id=session_id,
            agent_profile_id=agent_profile_id,
            created_from="initial",
        )
        return self.participants.create(
            workspace_id=session.workspace_id,
            session_id=session_id,
            binding_id=binding.binding_id,
            participant_role=participant_role,
            added_by=added_by,
        )

    def remove_participant(self, participant_id: str) -> SessionParticipant:
        removed = self.participants.set_removed(participant_id)
        if removed is None:
            raise ParticipantNotFoundError(f"Participant not found: {participant_id!r}")
        return removed

    def list_active_participants(self, session_id: str) -> List[SessionParticipant]:
        return self.participants.list_active(session_id)

    def list_active_participant_details(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT sp.participant_id, sp.session_id, sp.workspace_id, sp.binding_id, sp.role, sp.added_by, "
            "sp.added_at, sp.removed_at, apb.agent_profile_id, ap.name AS agent_name, "
            "ap.version AS agent_version, ap.provider_ref, ap.model_ref, ap.perspective_ref, ap.function_ref, ap.harness_ref, "
            "r.name AS role_name, r.description AS role_description, r.system_prompt AS role_system_prompt "
            "FROM session_participants sp "
            "JOIN agent_profile_bindings apb ON apb.binding_id = sp.binding_id "
            "JOIN agent_profiles ap ON ap.agent_profile_id = apb.agent_profile_id "
            "LEFT JOIN roles r ON r.role_id = ap.function_ref "
            "WHERE sp.session_id = ? AND sp.removed_at IS NULL "
            "ORDER BY sp.added_at ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_channel_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self.sessions.get_by_id(session_id)
        if session is None:
            return None
        for channel in self.channels.list_by_workspace(session.workspace_id):
            if channel.active_session_id == session_id:
                return {
                    "channel_id": channel.channel_id,
                    "workspace_id": channel.workspace_id,
                    "title": channel.title,
                    "channel_kind": channel.channel_kind,
                }
        return None
