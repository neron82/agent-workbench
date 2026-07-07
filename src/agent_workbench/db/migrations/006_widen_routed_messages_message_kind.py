"""006 — Widen routed_messages.message_kind CHECK.

Migration 005 added ``tool_confirmation_request`` and ``tool_result``
to ``VALID_MESSAGE_KINDS`` in the Python layer, but the underlying
SQLite CHECK on ``routed_messages.message_kind`` still rejects them.
Rebuild the table with the wider CHECK, the standard SQLite pattern.
"""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "006_widen_routed_messages_message_kind"
DESCRIPTION = "Widen routed_messages.message_kind CHECK to include tool message kinds"


def up(conn: sqlite3.Connection) -> None:
    # Idempotent probe: if the constraint already allows the new
    # values, do nothing.
    existing_check = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='routed_messages'"
    ).fetchone()
    if existing_check and "tool_confirmation_request" in (existing_check[0] or ""):
        return

    # Some test fixtures (in-memory DBs that get re-migrated after
    # the rename ran in a previous test) leave a stale
    # ``routed_messages_old`` table.  Drop it if present so the
    # rebuild is idempotent across re-runs.
    if conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='routed_messages_old'"
    ).fetchone():
        conn.execute("DROP TABLE routed_messages_old")

    # Copy existing rows, recreate the table with the wider CHECK.
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
                    -- New for tool-calling (migration 005+):
                    'tool_confirmation_request',
                    'tool_result'
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

    # SQLite RENAME does NOT update FK targets in referencing tables
    # (event_records.routed_message_id still points at
    # ``routed_messages_old``).  Rebuild event_records to refresh
    # the FK target.  Safe to skip on older schemas that don't have
    # a ``routed_message_id`` column.
    if conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='event_records'"
    ).fetchone():
        try:
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
        except sqlite3.OperationalError:
            # event_records doesn't have routed_message_id or doesn't
            # exist in this schema; safe to skip.
            pass
