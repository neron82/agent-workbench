"""016 — Index routed-message keyset history queries."""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_routed_messages_session_history "
        "ON routed_messages(session_id, created_at DESC, routed_message_id DESC)"
    )
    conn.commit()