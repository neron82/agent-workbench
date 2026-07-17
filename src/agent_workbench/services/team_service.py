"""TeamService — workspace-scoped reusable agent teams.

Provides CRUD for teams, member add/remove/list with workspace ownership
validation, duplicate prevention, and ``apply_team`` which returns ordered
profile IDs for downstream session integration.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from agent_workbench.models.agent_profile import AgentProfileRepository
from agent_workbench.models.agent_team import (
    AgentTeam,
    AgentTeamMember,
    AgentTeamRepository,
    AgentTeamMemberRepository,
)
from agent_workbench.models.workspace import WorkspaceRepository


class TeamNotFoundError(LookupError):
    """Raised when a team cannot be found."""


class TeamMemberNotFoundError(LookupError):
    """Raised when a team member or referenced entity cannot be found."""


class DuplicateTeamNameError(ValueError):
    """Raised when a team with the same name already exists in the workspace."""


class DuplicateTeamMemberError(ValueError):
    """Raised when the same agent profile is already a member of the team."""


class WorkspaceMismatchError(ValueError):
    """Raised when a workspace reference is invalid."""


class TeamService:
    """Service for managing workspace-scoped agent teams."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.teams = AgentTeamRepository(conn)
        self.members = AgentTeamMemberRepository(conn)
        self.workspaces = WorkspaceRepository(conn)
        self.profiles = AgentProfileRepository(conn)

    # ------------------------------------------------------------------
    # Team CRUD
    # ------------------------------------------------------------------

    def create_team(
        self,
        *,
        workspace_id: str,
        name: str,
        description: str = "",
    ) -> AgentTeam:
        """Create a new team in a workspace.

        Raises:
            TeamNotFoundError: if the workspace does not exist.
            DuplicateTeamNameError: if a team with the same name exists in the workspace.
        """
        if self.workspaces.get_by_id(workspace_id) is None:
            raise TeamNotFoundError(f"Workspace not found: {workspace_id!r}")

        try:
            return self.teams.create(
                workspace_id=workspace_id,
                name=name,
                description=description,
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateTeamNameError(
                    f"Team with name {name!r} already exists in workspace {workspace_id!r}"
                ) from exc
            raise

    def get_team(self, team_id: str) -> Optional[AgentTeam]:
        """Get a team by ID. Returns None if not found."""
        return self.teams.get_by_id(team_id)

    def list_teams(self, workspace_id: str) -> List[AgentTeam]:
        """List all teams in a workspace, ordered by name."""
        return self.teams.list_by_workspace(workspace_id)

    def update_team(
        self,
        team_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> AgentTeam:
        """Update a team's mutable fields.

        Raises:
            TeamNotFoundError: if the team does not exist.
            DuplicateTeamNameError: if the new name conflicts with an existing team.
        """
        existing = self.teams.get_by_id(team_id)
        if existing is None:
            raise TeamNotFoundError(f"Team not found: {team_id!r}")

        try:
            updated = self.teams.update(
                team_id,
                name=name,
                description=description,
            )
            assert updated is not None
            return updated
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateTeamNameError(
                    f"Team with name {name!r} already exists in workspace {existing.workspace_id!r}"
                ) from exc
            raise

    def delete_team(self, team_id: str) -> None:
        """Delete a team and cascade-delete its members.

        Raises:
            TeamNotFoundError: if the team does not exist.
        """
        if not self.teams.delete(team_id):
            raise TeamNotFoundError(f"Team not found: {team_id!r}")

    # ------------------------------------------------------------------
    # Member management
    # ------------------------------------------------------------------

    def add_member(
        self,
        *,
        team_id: str,
        agent_profile_id: str,
        role_label: str = "",
        sort_order: int = 0,
    ) -> AgentTeamMember:
        """Add an agent profile as a member of a team.

        Raises:
            TeamNotFoundError: if the team does not exist.
            TeamMemberNotFoundError: if the agent profile does not exist.
            DuplicateTeamMemberError: if the profile is already a member.
        """
        if self.teams.get_by_id(team_id) is None:
            raise TeamNotFoundError(f"Team not found: {team_id!r}")
        if self.profiles.get_by_id(agent_profile_id) is None:
            raise TeamMemberNotFoundError(
                f"AgentProfile not found: {agent_profile_id!r}"
            )

        try:
            return self.members.add(
                team_id=team_id,
                agent_profile_id=agent_profile_id,
                role_label=role_label,
                sort_order=sort_order,
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateTeamMemberError(
                    f"AgentProfile {agent_profile_id!r} is already a member of team {team_id!r}"
                ) from exc
            raise

    def remove_member(self, member_id: str) -> None:
        """Remove a member from a team.

        Raises:
            TeamMemberNotFoundError: if the member does not exist.
        """
        if not self.members.remove(member_id):
            raise TeamMemberNotFoundError(f"Team member not found: {member_id!r}")

    def list_members(self, team_id: str) -> List[AgentTeamMember]:
        """List all members of a team, ordered by sort_order."""
        return self.members.list_by_team(team_id)

    # ------------------------------------------------------------------
    # Apply team
    # ------------------------------------------------------------------

    def apply_team(self, team_id: str) -> List[str]:
        """Return the ordered list of agent profile IDs for a team.

        This is the integration point for applying a team to a session.
        Returns profile IDs in sort_order so the caller can add participants
        in the correct sequence.

        Raises:
            TeamNotFoundError: if the team does not exist.
        """
        team = self.teams.get_by_id(team_id)
        if team is None:
            raise TeamNotFoundError(f"Team not found: {team_id!r}")

        members = self.members.list_by_team(team_id)
        return [m.agent_profile_id for m in members]
