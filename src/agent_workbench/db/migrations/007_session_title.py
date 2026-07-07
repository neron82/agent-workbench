"""007 — Add title column to session_extensions for session naming.

Sessions currently have no title field; the title lives only on the
linked channel. This migration adds a nullable title column so sessions
can be named independently, and backfills existing sessions with their
channel title where available.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        ALTER TABLE session_extensions ADD COLUMN title TEXT;

        -- Backfill: copy channel title for sessions that have a channel link
        UPDATE session_extensions AS se
        SET title = (
            SELECT c.title FROM channels c
            WHERE c.active_session_id = se.session_id
            LIMIT 1
        )
        WHERE se.title IS NULL;
    """)
    conn.commit()
