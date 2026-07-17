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
        result = self.get_by_id(routed_message_id)
        assert result is not None
        return result

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

    def list_visible_before(
        self,
        session_id: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> tuple[list[RoutedMessage], Optional[str], bool]:
        """Return visible (non-dispatch) messages for a session using
        deterministic keyset pagination.

        Uses ``(created_at, routed_message_id)`` as the keyset so that
        equal timestamps are resolved deterministically by the primary key.

        Parameters
        ----------
        session_id:
            The session to fetch messages for.
        limit:
            Maximum number of messages to return (capped at 100).
        cursor:
            Opaque cursor string ``"created_at,routed_message_id"``
            representing the *oldest* message already seen. When provided,
            returns messages *older* than this cursor. When ``None``,
            returns the latest *limit* messages.

        Returns
        -------
        (messages, next_cursor, has_more)
            messages:
                The fetched messages in ascending chronological order.
            next_cursor:
                Opaque cursor string for the next page, or ``None`` if
                there are no more older messages.
            has_more:
                ``True`` if there are additional older messages beyond
                this page.
        """
        limit = max(1, min(limit, 100))

        if cursor is not None:
            # Parse cursor: "created_at,routed_message_id"
            parts = cursor.split(",", 1)
            if len(parts) != 2:
                raise ValueError("Malformed cursor")
            try:
                cursor_ts = float(parts[0])
            except (TypeError, ValueError):
                raise ValueError("Malformed cursor: invalid timestamp")
            cursor_id = parts[1]
            if not cursor_id:
                raise ValueError("Malformed cursor: missing message id")

            # Fetch limit+1 to detect has_more
            rows = self.conn.execute(
                "SELECT routed_message_id, workspace_id, session_id, channel_id, "
                "source_type, source_id, target_type, target_id, "
                "message_kind, payload_ref, created_at "
                "FROM routed_messages "
                "WHERE session_id = ? AND message_kind NOT IN ('dispatch', 'agent_work') "
                "AND (created_at < ? OR (created_at = ? AND routed_message_id < ?)) "
                "ORDER BY created_at DESC, routed_message_id DESC "
                "LIMIT ?",
                (session_id, cursor_ts, cursor_ts, cursor_id, limit + 1),
            ).fetchall()
        else:
            # Latest messages: fetch limit+1 to detect has_more
            rows = self.conn.execute(
                "SELECT routed_message_id, workspace_id, session_id, channel_id, "
                "source_type, source_id, target_type, target_id, "
                "message_kind, payload_ref, created_at "
                "FROM routed_messages "
                "WHERE session_id = ? AND message_kind NOT IN ('dispatch', 'agent_work') "
                "ORDER BY created_at DESC, routed_message_id DESC "
                "LIMIT ?",
                (session_id, limit + 1),
            ).fetchall()

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        # Reverse to ascending order for display
        messages = [self._row(r) for r in reversed(rows)]

        if not messages:
            return [], None, False

        # Build next cursor from the oldest message in this page
        # Only return a cursor if there are more older messages
        next_cursor: Optional[str] = None
        if has_more:
            oldest = messages[0]
            next_cursor = f"{oldest.created_at},{oldest.routed_message_id}"

        return messages, next_cursor, has_more

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
