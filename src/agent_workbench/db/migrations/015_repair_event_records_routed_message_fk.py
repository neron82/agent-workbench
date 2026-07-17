"""015 — Repair event_records FK that still targets routed_messages_old.

Migration 006 (widen_routed_messages_message_kind) renamed routed_messages
to routed_messages_old and recreated it, but the FK in event_records
still references routed_messages_old on databases where the 006 rebuild
of event_records was silently skipped (the try/except OperationalError
guard caught the case where event_records didn't have routed_message_id
yet, but also caught cases where the table existed with the stale FK).

This migration is idempotent:
- If event_records does not exist → no-op.
- If event_records already references routed_messages → no-op.
- If event_records references routed_messages_old → rebuild the table
  with the correct FK target, preserving all rows.
- Does NOT touch unrelated FK violations (e.g. session_participants →
  agent_profile_bindings).
- Does NOT mutate old migration records.
- Leaves no temporary table behind.
- Handles PRAGMA foreign_keys safely outside a transaction.
- Runs the DDL rebuild inside an explicit transaction so a failed copy rolls
  the schema changes back instead of stranding ``event_records_old``.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # ------------------------------------------------------------------
    # 1. Probe: does event_records exist?
    # ------------------------------------------------------------------
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='event_records'"
    ).fetchone()
    if row is None:
        return  # table absent → no-op

    # ------------------------------------------------------------------
    # 2. Probe: does the FK already target routed_messages?
    # ------------------------------------------------------------------
    fk_list = conn.execute(
        "PRAGMA foreign_key_list(event_records)"
    ).fetchall()
    # fk_list columns: id, seq, table, from, to, on_update, on_delete, match
    targets_routed_messages_old = any(
        r["table"] == "routed_messages_old" for r in fk_list
    )
    if not targets_routed_messages_old:
        return  # FK already correct (or no FK at all) → no-op

    # ------------------------------------------------------------------
    # 3. Save PRAGMA foreign_keys state, disable it outside a transaction
    # ------------------------------------------------------------------
    conn.commit()  # ensure we're outside any active transaction
    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.commit()  # PRAGMA is a no-op inside a transaction

    try:
        # sqlite3 does not implicitly open a transaction for DDL.  Start one
        # explicitly so ALTER/CREATE/DROP are atomic and rollback-safe.
        conn.execute("BEGIN IMMEDIATE")

        # ------------------------------------------------------------------
        # 4. Rebuild event_records with canonical columns + correct FK
        # ------------------------------------------------------------------
        conn.execute("DROP TABLE IF EXISTS event_records_old")
        conn.execute("ALTER TABLE event_records RENAME TO event_records_old")

        conn.execute(
            """\
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
            """\
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
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        # Restore PRAGMA foreign_keys to its original state.
        # Must be outside a transaction.
        conn.execute("PRAGMA foreign_keys = ON" if fk_was_on else "PRAGMA foreign_keys = OFF")
        conn.commit()
