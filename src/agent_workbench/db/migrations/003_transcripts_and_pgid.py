"""003 — Persistent transcripts and process-group tracking for harness runs.

What it adds
------------
1. New table ``harness_transcripts``:
     - one row per stdout/stderr line per harness run
     - indexed by ``harness_run_id`` and ``timestamp``
2. New columns on ``harness_runs``:
     - ``pgid``        TEXT  : the OS process group id (for local harnesses)
     - ``exit_code``   INTEGER : the process exit code
     - ``exit_signal`` INTEGER : the signal that terminated the process
3. New table ``harness_events``:
     - append-only event log (start, stop, cancel, status change, transcript flush)
     - used to reconstruct the lifecycle of a run after a server restart

All changes are additive: existing tables keep their original columns and
constraints.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # -----------------------------------------------------------------------
    # harness_transcripts: durable, append-only transcript lines
    # -----------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS harness_transcripts (
            transcript_id    TEXT PRIMARY KEY,
            harness_run_id   TEXT NOT NULL REFERENCES harness_runs(harness_run_id),
            line_no          INTEGER NOT NULL DEFAULT 0,
            stream           TEXT NOT NULL CHECK (stream IN ('stdout', 'stderr')),
            content          TEXT NOT NULL DEFAULT '',
            captured_at      REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_harness_transcripts_run "
        "ON harness_transcripts(harness_run_id, captured_at)"
    )

    # -----------------------------------------------------------------------
    # harness_runs: pgid + exit_code + exit_signal
    # -----------------------------------------------------------------------
    # ``ALTER TABLE`` does not support ``IF NOT EXISTS`` on every SQLite
    # version, so we probe pragma table_info for a column before adding it.
    existing_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(harness_runs)").fetchall()
    }
    for col_name, ddl in (
        ("pgid",        "ALTER TABLE harness_runs ADD COLUMN pgid TEXT"),
        ("exit_code",   "ALTER TABLE harness_runs ADD COLUMN exit_code INTEGER"),
        ("exit_signal", "ALTER TABLE harness_runs ADD COLUMN exit_signal INTEGER"),
    ):
        if col_name not in existing_cols:
            conn.execute(ddl)

    # -----------------------------------------------------------------------
    # harness_events: append-only lifecycle log per run
    # -----------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS harness_events (
            event_id         TEXT PRIMARY KEY,
            harness_run_id   TEXT NOT NULL REFERENCES harness_runs(harness_run_id),
            event_type       TEXT NOT NULL
                             CHECK (event_type IN (
                                 'start', 'status_change', 'transcript_flush',
                                 'stop', 'cancel', 'exit'
                             )),
            detail_json      TEXT NOT NULL DEFAULT '{}',
            created_at       REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_harness_events_run "
        "ON harness_events(harness_run_id, created_at)"
    )
