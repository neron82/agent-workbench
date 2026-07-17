"""012 — Repair sessions that were created without a routable channel.

The alpha continuation workflow originally created a session extension but
forgot to create the channel required by the runtime message router. This
idempotent data migration repairs existing orphan sessions; new continuations
create their channel in ParticipantTransferService.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO channels (
            channel_id,
            workspace_id,
            channel_kind,
            title,
            active_session_id,
            status,
            created_at,
            updated_at
        )
        SELECT
            lower(hex(randomblob(16))),
            se.workspace_id,
            se.session_type,
            COALESCE(se.title, ''),
            se.session_id,
            CASE WHEN se.status = 'archived' THEN 'archived' ELSE 'active' END,
            se.created_at,
            se.created_at
        FROM session_extensions AS se
        WHERE NOT EXISTS (
            SELECT 1
            FROM channels AS c
            WHERE c.active_session_id = se.session_id
        )
        """
    )
    conn.commit()
