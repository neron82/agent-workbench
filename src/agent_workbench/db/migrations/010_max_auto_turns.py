"""010 — Add per-session max_auto_turns for iterative agent discussion.

When max_auto_turns > 0, agents can respond to each other's @mentions
in a chain. Each turn picks up the last @mention and dispatches only
the mentioned agent. Default 0 = off (current behaviour).

Defaults (applied in SessionService.create_session):
  chat     = 0  (off — simple broadcast)
  research = 3  (allow a few turns of back-and-forth)
  work     = 5  (longer chains for structured work)
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        ALTER TABLE session_extensions ADD COLUMN max_auto_turns INTEGER DEFAULT 0;
    """)
    conn.commit()