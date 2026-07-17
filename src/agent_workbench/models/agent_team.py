"""Agent team domain model and repository.

An agent team is a reusable, workspace-scoped group of agent profiles that
can be applied to a session to add all members at once with their assigned
roles and ordering.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class AgentTeam:
    """A reusable, workspace-scoped group of agent profiles."""

    team_id: str
    workspace_id: str
    name: str
    description: str
    created_at: float
    updated_at: float


@dataclass
class AgentTeamMember:
    """A member (agent profile) belonging to a team with a role and sort order."""

    member_id: str
    team_id: str
    agent_profile_id: str
    role_label: str
    sort_order: int
    created_at: float


class AgentTeamRepository:
    """SQLite-backed repository for AgentTeam entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        workspace_id: str,
        name: str,
        description: str = "",
    ) -> AgentTeam:
        """Insert a new agent team and return the persisted instance."""
        team_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO agent_teams (team_id, workspace_id, name, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (team_id, workspace_id, name, description, now, now),
        )
        self.conn.commit()
        return AgentTeam(
            team_id=team_id,
            workspace_id=workspace_id,
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
        )

    def get_by_id(self, team_id: str) -> Optional[AgentTeam]:
        row = self.conn.execute(
            "SELECT team_id, workspace_id, name, description, created_at, updated_at "
            "FROM agent_teams WHERE team_id = ?",
            (team_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_workspace(self, workspace_id: str) -> List[AgentTeam]:
        rows = self.conn.execute(
            "SELECT team_id, workspace_id, name, description, created_at, updated_at "
            "FROM agent_teams WHERE workspace_id = ? ORDER BY name ASC",
            (workspace_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update(
        self,
        team_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[AgentTeam]:
        """Update mutable fields on a team. Returns the updated instance or None."""
        updates: list[str] = ["updated_at = ?"]
        params: list = [time.time()]

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)

        params.append(team_id)
        self.conn.execute(
            f"UPDATE agent_teams SET {', '.join(updates)} WHERE team_id = ?",
            params,
        )
        self.conn.commit()
        return self.get_by_id(team_id)

    def delete(self, team_id: str) -> bool:
        """Delete a team. Members cascade-delete. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM agent_teams WHERE team_id = ?",
            (team_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> AgentTeam:
        return AgentTeam(
            team_id=row["team_id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            description=row["description"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class AgentTeamMemberRepository:
    """SQLite-backed repository for AgentTeamMember entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(
        self,
        *,
        team_id: str,
        agent_profile_id: str,
        role_label: str = "",
        sort_order: int = 0,
    ) -> AgentTeamMember:
        """Add a member to a team. Raises IntegrityError on duplicate (team, profile)."""
        member_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO agent_team_members (member_id, team_id, agent_profile_id, role_label, sort_order, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (member_id, team_id, agent_profile_id, role_label, sort_order, now),
        )
        self.conn.commit()
        return AgentTeamMember(
            member_id=member_id,
            team_id=team_id,
            agent_profile_id=agent_profile_id,
            role_label=role_label,
            sort_order=sort_order,
            created_at=now,
        )

    def get_by_id(self, member_id: str) -> Optional[AgentTeamMember]:
        row = self.conn.execute(
            "SELECT member_id, team_id, agent_profile_id, role_label, sort_order, created_at "
            "FROM agent_team_members WHERE member_id = ?",
            (member_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_team(self, team_id: str) -> List[AgentTeamMember]:
        rows = self.conn.execute(
            "SELECT member_id, team_id, agent_profile_id, role_label, sort_order, created_at "
            "FROM agent_team_members WHERE team_id = ? ORDER BY sort_order ASC",
            (team_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def remove(self, member_id: str) -> bool:
        """Remove a member from a team. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM agent_team_members WHERE member_id = ?",
            (member_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> AgentTeamMember:
        return AgentTeamMember(
            member_id=row["member_id"],
            team_id=row["team_id"],
            agent_profile_id=row["agent_profile_id"],
            role_label=row["role_label"],
            sort_order=row["sort_order"],
            created_at=row["created_at"],
        )
