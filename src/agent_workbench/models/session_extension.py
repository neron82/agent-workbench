"""SessionExtension domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional

SESSION_TYPES = ("chat", "research", "work")
SESSION_STATUSES = (
    "active",
    "waiting_review",
    "waiting_approval",
    "done",
    "blocked",
    "failed",
    "archived",
)


@dataclass
class SessionExtension:
    session_id: str
    workspace_id: str
    session_type: str
    agent_profile_binding_id: Optional[str]
    fork_id: Optional[str]
    task_spec_id: Optional[str]
    status: str
    title: Optional[str]
    max_tool_iterations: Optional[int]
    created_at: float


class SessionExtensionRepository:
    """SQLite-backed repository for SessionExtension entities.

    Enforces immutability of ``session_type`` at the repository level — any
    attempt to update it raises ``ValueError``.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        workspace_id: str,
        session_type: str,
        agent_profile_binding_id: Optional[str] = None,
        fork_id: Optional[str] = None,
        task_spec_id: Optional[str] = None,
        status: str = "active",
        title: Optional[str] = None,
        max_tool_iterations: Optional[int] = None,
    ) -> SessionExtension:
        """Insert a new session extension and return the persisted instance."""
        if session_type not in SESSION_TYPES:
            raise ValueError(
                f"Invalid session_type: {session_type!r}. "
                f"Must be one of {SESSION_TYPES}"
            )
        if status not in SESSION_STATUSES:
            raise ValueError(
                f"Invalid status: {status!r}. Must be one of {SESSION_STATUSES}"
            )

        session_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO session_extensions "
            "(session_id, workspace_id, session_type, agent_profile_binding_id, "
            "fork_id, task_spec_id, status, title, max_tool_iterations, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                workspace_id,
                session_type,
                agent_profile_binding_id,
                fork_id,
                task_spec_id,
                status,
                title,
                max_tool_iterations,
                created_at,
            ),
        )
        self.conn.commit()
        return SessionExtension(
            session_id=session_id,
            workspace_id=workspace_id,
            session_type=session_type,
            agent_profile_binding_id=agent_profile_binding_id,
            fork_id=fork_id,
            task_spec_id=task_spec_id,
            status=status,
            title=title,
            max_tool_iterations=max_tool_iterations,
            created_at=created_at,
        )

    def get_by_id(self, session_id: str) -> Optional[SessionExtension]:
        row = self.conn.execute(
            "SELECT session_id, workspace_id, session_type, "
            "agent_profile_binding_id, fork_id, task_spec_id, status, title, "
            "max_tool_iterations, created_at "
            "FROM session_extensions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def get_by_session_id(self, session_id: str) -> Optional[SessionExtension]:
        """Alias for :meth:`get_by_id` — kept for API clarity."""
        return self.get_by_id(session_id)

    def list_by_workspace(self, workspace_id: str) -> List[SessionExtension]:
        rows = self.conn.execute(
            "SELECT session_id, workspace_id, session_type, "
            "agent_profile_binding_id, fork_id, task_spec_id, status, title, "
            "max_tool_iterations, created_at "
            "FROM session_extensions WHERE workspace_id = ? ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update_status(
        self,
        session_id: str,
        *,
        status: str,
    ) -> Optional[SessionExtension]:
        """Update only the status of a session extension.

        Enforces immutability of ``session_type`` — it cannot be changed via
        any update method on this repository.

        Returns the updated instance or ``None`` if not found.
        """
        if status not in SESSION_STATUSES:
            raise ValueError(
                f"Invalid status: {status!r}. Must be one of {SESSION_STATUSES}"
            )
        cursor = self.conn.execute(
            "UPDATE session_extensions SET status = ? WHERE session_id = ?",
            (status, session_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(session_id)

    def update_task_spec(
        self,
        session_id: str,
        *,
        task_spec_id: Optional[str],
    ) -> Optional[SessionExtension]:
        """Update the task_spec_id of a session extension."""
        cursor = self.conn.execute(
            "UPDATE session_extensions SET task_spec_id = ? WHERE session_id = ?",
            (task_spec_id, session_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(session_id)

    def update_title(
        self,
        session_id: str,
        *,
        title: Optional[str],
    ) -> Optional[SessionExtension]:
        """Update the title of a session extension."""
        cursor = self.conn.execute(
            "UPDATE session_extensions SET title = ? WHERE session_id = ?",
            (title, session_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(session_id)

    def update_max_tool_iterations(
        self,
        session_id: str,
        *,
        max_tool_iterations: Optional[int],
    ) -> Optional[SessionExtension]:
        """Update the max_tool_iterations of a session extension."""
        cursor = self.conn.execute(
            "UPDATE session_extensions SET max_tool_iterations = ? WHERE session_id = ?",
            (max_tool_iterations, session_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(session_id)

    def delete(self, session_id: str) -> bool:
        """Delete a session extension. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM session_extensions WHERE session_id = ?",
            (session_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> SessionExtension:
        return SessionExtension(
            session_id=row["session_id"],
            workspace_id=row["workspace_id"],
            session_type=row["session_type"],
            agent_profile_binding_id=row["agent_profile_binding_id"],
            fork_id=row["fork_id"],
            task_spec_id=row["task_spec_id"],
            status=row["status"],
            title=row["title"],
            max_tool_iterations=row["max_tool_iterations"],
            created_at=row["created_at"],
        )
