"""Agent profile binding domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class AgentProfileBinding:
    binding_id: str
    session_id: str
    agent_profile_id: str
    binding_version: str
    created_from: str
    created_at: float


class AgentProfileBindingRepository:
    """SQLite-backed repository for AgentProfileBinding entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        session_id: str,
        agent_profile_id: str,
        binding_version: str = "1",
        created_from: str = "initial",
    ) -> AgentProfileBinding:
        """Insert a new binding and return the persisted instance."""
        binding_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO agent_profile_bindings ("
            "binding_id, session_id, agent_profile_id, binding_version, created_from, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                binding_id,
                session_id,
                agent_profile_id,
                binding_version,
                created_from,
                created_at,
            ),
        )
        self.conn.commit()
        return AgentProfileBinding(
            binding_id=binding_id,
            session_id=session_id,
            agent_profile_id=agent_profile_id,
            binding_version=binding_version,
            created_from=created_from,
            created_at=created_at,
        )

    def get_by_id(self, binding_id: str) -> Optional[AgentProfileBinding]:
        row = self.conn.execute(
            "SELECT binding_id, session_id, agent_profile_id, binding_version, "
            "created_from, created_at "
            "FROM agent_profile_bindings WHERE binding_id = ?",
            (binding_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def get_by_session(self, session_id: str) -> List[AgentProfileBinding]:
        """Return all bindings for a session, newest first."""
        rows = self.conn.execute(
            "SELECT binding_id, session_id, agent_profile_id, binding_version, "
            "created_from, created_at "
            "FROM agent_profile_bindings WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def get_latest_for_session(self, session_id: str) -> Optional[AgentProfileBinding]:
        """Return the most recent binding for a given session."""
        row = self.conn.execute(
            "SELECT binding_id, session_id, agent_profile_id, binding_version, "
            "created_from, created_at "
            "FROM agent_profile_bindings WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def delete(self, binding_id: str) -> bool:
        """Delete a binding. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM agent_profile_bindings WHERE binding_id = ?",
            (binding_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> AgentProfileBinding:
        return AgentProfileBinding(
            binding_id=row["binding_id"],
            session_id=row["session_id"],
            agent_profile_id=row["agent_profile_id"],
            binding_version=row["binding_version"],
            created_from=row["created_from"],
            created_at=row["created_at"],
        )
