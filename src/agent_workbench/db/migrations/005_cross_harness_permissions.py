"""005 — Cross-harness permission table.

When an agent whose configured harness is, say, ``hermes`` calls a
tool whose harness is ``shell`` (or anything other than ``hermes``),
we cannot execute that call without user confirmation.  This migration
adds two things:

1. ``cross_harness_permissions`` — per-session whitelist of
   ``(agent_harness_type, tool_harness_type)`` pairs the user has
   already approved.  ``agent_harness_type IS NULL`` means "approved
   for any agent harness", which we treat as a global "yes permanent"
   for the session.

   ``decision`` distinguishes:

   - ``once``      — a single call is allowed; row is deleted after
   - ``permanent`` — all future calls of this kind are auto-approved
                     until the session ends

2. New column ``tool_invocations.requires_confirmation`` plus
   ``tool_invocations.confirmation_reason`` so the UI can show the
   user *why* a call is waiting.  The ``status`` column gets two
   new values: ``pending_confirmation`` and ``denied`` (the latter
   already existed but is now used both by the dispatcher and the
   confirmation endpoint).
"""

from __future__ import annotations

import sqlite3
import time

MIGRATION_ID = "005_cross_harness_permissions"
DESCRIPTION = "Cross-harness confirmation permissions + pending_confirmation status"


def up(conn: sqlite3.Connection) -> None:
    now = time.time()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cross_harness_permissions (
            permission_id        TEXT PRIMARY KEY,
            session_id           TEXT NOT NULL,
            workspace_id         TEXT NOT NULL,
            agent_harness_type   TEXT,                 -- nullable = "any agent"
            tool_harness_type    TEXT NOT NULL,
            decision             TEXT NOT NULL
                CHECK (decision IN ('once', 'permanent')),
            created_at           REAL NOT NULL,
            consumed_at          REAL,                 -- set when a 'once' fires
            expires_at           REAL,                 -- optional, for future TTL
            UNIQUE (session_id, agent_harness_type, tool_harness_type, decision)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cross_harness_permissions_session
            ON cross_harness_permissions(session_id)
        """
    )

    # The CHECK on tool_invocations.status was set in migration 004 to
    # exactly the values present there.  SQLite allows us to drop and
    # recreate the constraint, but CHECKs aren't easily modified.  The
    # migration framework stores the version, so we just create a
    # standalone ``tool_invocations_status_check`` would collide with
    # the auto-named one.  Instead we accept that ``pending_confirmation``
    # is a runtime-only status — SQLite's CHECK will reject it, so we
    # have to relax the CHECK first.
    #
    # Strategy: rebuild the table by renaming, creating a new one
    # with the wider CHECK, copying the data over, and dropping the
    # old one.  This is the standard SQLite idiom.
    existing_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(tool_invocations)").fetchall()
    }
    if "requires_confirmation" not in existing_columns:
        conn.execute("ALTER TABLE tool_invocations RENAME TO tool_invocations_old")
        conn.execute(
            """
            CREATE TABLE tool_invocations (
                invocation_id    TEXT PRIMARY KEY,
                session_id       TEXT NOT NULL,
                workspace_id     TEXT NOT NULL,
                tool_id          TEXT NOT NULL REFERENCES tools(tool_id),
                tool_name        TEXT NOT NULL,
                tool_harness_type TEXT NOT NULL DEFAULT '',
                arguments_json   TEXT NOT NULL DEFAULT '{}',
                status           TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'running', 'completed',
                        'failed', 'denied', 'pending_confirmation'
                    )),
                result_text      TEXT,
                error_text       TEXT,
                harness_run_id   TEXT REFERENCES harness_runs(harness_run_id),
                confirmation_message_id TEXT,
                requires_confirmation INTEGER NOT NULL DEFAULT 0,
                confirmation_reason TEXT,
                created_at       REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                completed_at     REAL
            )
            """
        )
        # Copy all rows, supplying defaults for the new columns.
        conn.execute(
            """
            INSERT INTO tool_invocations (
                invocation_id, session_id, workspace_id, tool_id, tool_name,
                tool_harness_type, arguments_json, status, result_text, error_text,
                harness_run_id, confirmation_message_id, requires_confirmation,
                confirmation_reason, created_at, completed_at
            )
            SELECT
                invocation_id, session_id, workspace_id, tool_id, tool_name,
                '' AS tool_harness_type,
                arguments_json, status, result_text, error_text,
                harness_run_id, NULL AS confirmation_message_id,
                0 AS requires_confirmation,
                NULL AS confirmation_reason,
                created_at, completed_at
            FROM tool_invocations_old
            """
        )
        conn.execute("DROP TABLE tool_invocations_old")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tool_invocations_session
                ON tool_invocations(session_id, created_at)
            """
        )
