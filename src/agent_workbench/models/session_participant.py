"""Session participant domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


PARTICIPANT_ROLES = ("member", "silent")
PARTICIPANT_ADDED_BY = ("user", "orchestrator", "system")


@dataclass
class SessionParticipant:
    participant_id: str
    workspace_id: str
    session_id: str
    binding_id: str
    participant_role: str
    added_by: str
    added_at: float
    removed_at: Optional[float]


class SessionParticipantRepository:
    """SQLite-backed repository for session participants."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        workspace_id: str,
        session_id: str,
        binding_id: str,
        participant_role: str = "member",
        added_by: str = "user",
    ) -> SessionParticipant:
        if participant_role not in PARTICIPANT_ROLES:
            raise ValueError(
                f"Invalid participant_role: {participant_role!r}. Must be one of {PARTICIPANT_ROLES}"
            )
        if added_by not in PARTICIPANT_ADDED_BY:
            raise ValueError(
                f"Invalid added_by: {added_by!r}. Must be one of {PARTICIPANT_ADDED_BY}"
            )
        participant_id = uuid.uuid4().hex
        added_at = time.time()
        self.conn.execute(
            "INSERT INTO session_participants "
            "(participant_id, workspace_id, session_id, binding_id, role, added_by, added_at, removed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                participant_id,
                workspace_id,
                session_id,
                binding_id,
                participant_role,
                added_by,
                added_at,
            ),
        )
        self.conn.commit()
        return self.get_by_id(participant_id)  # type: ignore[return-value]

    def get_by_id(self, participant_id: str) -> Optional[SessionParticipant]:
        row = self.conn.execute(
            "SELECT participant_id, workspace_id, session_id, binding_id, role, added_by, added_at, removed_at "
            "FROM session_participants WHERE participant_id = ?",
            (participant_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_active(self, session_id: str) -> List[SessionParticipant]:
        rows = self.conn.execute(
            "SELECT participant_id, workspace_id, session_id, binding_id, role, added_by, added_at, removed_at "
            "FROM session_participants WHERE session_id = ? AND removed_at IS NULL ORDER BY added_at ASC",
            (session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_for_session(self, session_id: str) -> List[SessionParticipant]:
        rows = self.conn.execute(
            "SELECT participant_id, workspace_id, session_id, binding_id, role, added_by, added_at, removed_at "
            "FROM session_participants WHERE session_id = ? ORDER BY added_at ASC",
            (session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def set_removed(self, participant_id: str) -> Optional[SessionParticipant]:
        now = time.time()
        cursor = self.conn.execute(
            "UPDATE session_participants SET removed_at = ? WHERE participant_id = ? AND removed_at IS NULL",
            (now, participant_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(participant_id)

    def reactivate(self, participant_id: str) -> Optional[SessionParticipant]:
        cursor = self.conn.execute(
            "UPDATE session_participants SET removed_at = NULL WHERE participant_id = ?",
            (participant_id,),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(participant_id)

    @staticmethod
    def _row(row: sqlite3.Row) -> SessionParticipant:
        return SessionParticipant(
            participant_id=row["participant_id"],
            workspace_id=row["workspace_id"],
            session_id=row["session_id"],
            binding_id=row["binding_id"],
            participant_role=row["role"],
            added_by=row["added_by"],
            added_at=row["added_at"],
            removed_at=row["removed_at"],
        )
