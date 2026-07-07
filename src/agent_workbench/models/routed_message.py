"""Routed message domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RoutedMessage:
    routed_message_id: str
    workspace_id: str
    session_id: Optional[str]
    channel_id: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    message_kind: str
    payload_ref: Optional[str]
    created_at: float


class RoutedMessageRepository:
    """SQLite-backed repository for RoutedMessage entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        workspace_id: str,
        channel_id: str,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        message_kind: str,
        session_id: Optional[str] = None,
        payload_ref: Optional[str] = None,
    ) -> RoutedMessage:
        """Insert a new routed message and return the persisted instance."""
        routed_message_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO routed_messages "
            "(routed_message_id, workspace_id, session_id, channel_id, "
            "source_type, source_id, target_type, target_id, "
            "message_kind, payload_ref, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                routed_message_id,
                workspace_id,
                session_id,
                channel_id,
                source_type,
                source_id,
                target_type,
                target_id,
                message_kind,
                payload_ref,
                created_at,
            ),
        )
        self.conn.commit()
        return self.get_by_id(routed_message_id)

    def get_by_id(self, routed_message_id: str) -> Optional[RoutedMessage]:
        row = self.conn.execute(
            "SELECT routed_message_id, workspace_id, session_id, channel_id, "
            "source_type, source_id, target_type, target_id, "
            "message_kind, payload_ref, created_at "
            "FROM routed_messages WHERE routed_message_id = ?",
            (routed_message_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_channel(self, channel_id: str) -> List[RoutedMessage]:
        rows = self.conn.execute(
            "SELECT routed_message_id, workspace_id, session_id, channel_id, "
            "source_type, source_id, target_type, target_id, "
            "message_kind, payload_ref, created_at "
            "FROM routed_messages WHERE channel_id = ? ORDER BY created_at ASC",
            (channel_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_by_session(self, session_id: str) -> List[RoutedMessage]:
        rows = self.conn.execute(
            "SELECT routed_message_id, workspace_id, session_id, channel_id, "
            "source_type, source_id, target_type, target_id, "
            "message_kind, payload_ref, created_at "
            "FROM routed_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_by_target(
        self, target_type: str, target_id: str
    ) -> List[RoutedMessage]:
        rows = self.conn.execute(
            "SELECT routed_message_id, workspace_id, session_id, channel_id, "
            "source_type, source_id, target_type, target_id, "
            "message_kind, payload_ref, created_at "
            "FROM routed_messages WHERE target_type = ? AND target_id = ? "
            "ORDER BY created_at ASC",
            (target_type, target_id),
        ).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, routed_message_id: str) -> bool:
        """Delete a routed message. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM routed_messages WHERE routed_message_id = ?",
            (routed_message_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> RoutedMessage:
        return RoutedMessage(
            routed_message_id=row["routed_message_id"],
            workspace_id=row["workspace_id"],
            session_id=row["session_id"],
            channel_id=row["channel_id"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            message_kind=row["message_kind"],
            payload_ref=row["payload_ref"],
            created_at=row["created_at"],
        )
