"""008 — Add per-session max_tool_iterations override.

Sessions can now override the global MAX_TOOL_ITERATIONS (5) with a
per-session limit. Defaults by session type are applied at creation
time in the service layer, not in the migration.

Defaults (applied in SessionService.create_session):
  chat     = 5
  research = 10
  work     = 25
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        ALTER TABLE session_extensions ADD COLUMN max_tool_iterations INTEGER;
    """)
    conn.commit()
