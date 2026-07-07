"""Channel domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional

CHANNEL_KINDS = ("chat", "research", "work", "review", "system")
CHANNEL_STATUSES = ("active", "paused", "stopped", "archived")


@dataclass
class Channel:
    channel_id: str
    workspace_id: str
    channel_kind: str
    title: str
    active_session_id: Optional[str]
    default_target: Optional[str]
    status: str
    created_at: float
    updated_at: float


class ChannelRepository:
    """SQLite-backed repository for Channel entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        workspace_id: str,
        channel_kind: str,
        title: str = "",
        active_session_id: Optional[str] = None,
        default_target: Optional[str] = None,
        status: str = "active",
    ) -> Channel:
        """Insert a new channel and return the persisted instance."""
        if channel_kind not in CHANNEL_KINDS:
            raise ValueError(
                f"Invalid channel_kind: {channel_kind!r}. "
                f"Must be one of {CHANNEL_KINDS}"
            )
        if status not in CHANNEL_STATUSES:
            raise ValueError(
                f"Invalid status: {status!r}. Must be one of {CHANNEL_STATUSES}"
            )

        channel_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO channels "
            "(channel_id, workspace_id, channel_kind, title, active_session_id, "
            "default_target, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                channel_id,
                workspace_id,
                channel_kind,
                title,
                active_session_id,
                default_target,
                status,
                now,
                now,
            ),
        )
        self.conn.commit()
        return Channel(
            channel_id=channel_id,
            workspace_id=workspace_id,
            channel_kind=channel_kind,
            title=title,
            active_session_id=active_session_id,
            default_target=default_target,
            status=status,
            created_at=now,
            updated_at=now,
        )

    def get_by_id(self, channel_id: str) -> Optional[Channel]:
        row = self.conn.execute(
            "SELECT channel_id, workspace_id, channel_kind, title, "
            "active_session_id, default_target, status, created_at, updated_at "
            "FROM channels WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_workspace(self, workspace_id: str) -> List[Channel]:
        rows = self.conn.execute(
            "SELECT channel_id, workspace_id, channel_kind, title, "
            "active_session_id, default_target, status, created_at, updated_at "
            "FROM channels WHERE workspace_id = ? ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update_status(
        self,
        channel_id: str,
        *,
        status: str,
    ) -> Optional[Channel]:
        """Update only the status of a channel. Returns the updated instance or None."""
        if status not in CHANNEL_STATUSES:
            raise ValueError(
                f"Invalid status: {status!r}. Must be one of {CHANNEL_STATUSES}"
            )
        now = time.time()
        cursor = self.conn.execute(
            "UPDATE channels SET status = ?, updated_at = ? WHERE channel_id = ?",
            (status, now, channel_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(channel_id)

    def update_active_session(
        self,
        channel_id: str,
        *,
        active_session_id: Optional[str],
    ) -> Optional[Channel]:
        """Update the active_session_id of a channel."""
        now = time.time()
        cursor = self.conn.execute(
            "UPDATE channels SET active_session_id = ?, updated_at = ? "
            "WHERE channel_id = ?",
            (active_session_id, now, channel_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(channel_id)

    def delete(self, channel_id: str) -> bool:
        """Delete a channel. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM channels WHERE channel_id = ?",
            (channel_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> Channel:
        return Channel(
            channel_id=row["channel_id"],
            workspace_id=row["workspace_id"],
            channel_kind=row["channel_kind"],
            title=row["title"],
            active_session_id=row["active_session_id"],
            default_target=row["default_target"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
