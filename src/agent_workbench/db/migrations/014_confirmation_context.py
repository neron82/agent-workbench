"""014 — Add confirmation_context_json to tool_invocations.

Stores the exact original confirmation context (agent_harness_type,
session_policy preserving None vs [], allowed_tool_names preserving
None vs []) so the confirmation POST can redispatch using only stored
context, ignoring any posted session_policy.

Idempotent: uses ALTER TABLE ... ADD COLUMN with IF NOT EXISTS guard.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # Check if column already exists (idempotent)
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(tool_invocations)").fetchall()
    }
    if "confirmation_context_json" not in cols:
        conn.execute(
            "ALTER TABLE tool_invocations "
            "ADD COLUMN confirmation_context_json TEXT"
        )
    conn.commit()
