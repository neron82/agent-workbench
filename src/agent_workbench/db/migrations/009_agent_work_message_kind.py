"""009 — Add agent_work to routed_messages.message_kind CHECK.

The agent runtime now persists each tool-calling step as a routed_message
with message_kind='agent_work' so the chat history shows "work done"
bubbles that survive page reloads.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # Idempotent probe
    existing = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='routed_messages'"
    ).fetchone()
    if existing and "agent_work" in (existing[0] or ""):
        return

    # Disable FK enforcement during the table rebuild so the
    # intermediate state (old table renamed, new table not yet
    # populated) doesn't trigger constraint violations.
    conn.execute("PRAGMA foreign_keys = OFF")

    # Drop stale old table if present
    if conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='routed_messages_old'"
    ).fetchone():
        conn.execute("DROP TABLE routed_messages_old")

    conn.execute("ALTER TABLE routed_messages RENAME TO routed_messages_old")
    conn.execute(
        """
        CREATE TABLE routed_messages (
            routed_message_id TEXT PRIMARY KEY,
            workspace_id      TEXT NOT NULL REFERENCES workspaces(workspace_id),
            session_id        TEXT,
            channel_id        TEXT NOT NULL,
            source_type       TEXT NOT NULL,
            source_id         TEXT NOT NULL,
            target_type       TEXT NOT NULL,
            target_id         TEXT NOT NULL,
            message_kind      TEXT NOT NULL
                CHECK (message_kind IN (
                    'conversation', 'dispatch', 'steering', 'report',
                    'system', 'telemetry',
                    'tool_confirmation_request',
                    'tool_result',
                    'agent_work'
                )),
            payload_ref       TEXT,
            created_at        REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        )
        """
    )
    conn.execute(
        """
        INSERT INTO routed_messages (
            routed_message_id, workspace_id, session_id, channel_id,
            source_type, source_id, target_type, target_id,
            message_kind, payload_ref, created_at
        )
        SELECT
            routed_message_id, workspace_id, session_id, channel_id,
            source_type, source_id, target_type, target_id,
            message_kind, payload_ref, created_at
        FROM routed_messages_old
        """
    )
    conn.execute("DROP TABLE routed_messages_old")

    # Rebuild event_records to refresh FK target (same pattern as 006).
    # Must happen AFTER the routed_messages rename so the new table exists.
    has_event_records = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='event_records'"
    ).fetchone()
    if has_event_records:
        # Check if event_records has a routed_message_id column
        cols = [r[1] for r in conn.execute("PRAGMA table_info(event_records)")]
        if "routed_message_id" in cols:
            conn.execute("ALTER TABLE event_records RENAME TO event_records_old")
            conn.execute(
                """
                CREATE TABLE event_records (
                    event_id            TEXT PRIMARY KEY,
                    harness_run_id      TEXT REFERENCES harness_runs(harness_run_id),
                    routed_message_id   TEXT REFERENCES routed_messages(routed_message_id),
                    event_type          TEXT NOT NULL,
                    event_source        TEXT NOT NULL,
                    event_payload_ref   TEXT,
                    event_ts            REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO event_records (
                    event_id, harness_run_id, routed_message_id,
                    event_type, event_source, event_payload_ref, event_ts
                )
                SELECT
                    event_id, harness_run_id, routed_message_id,
                    event_type, event_source, event_payload_ref, event_ts
                FROM event_records_old
                """
            )
            conn.execute("DROP TABLE event_records_old")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
