"""004 — Tool definitions and tool-invocation tracking.

Adds
----
1. New table ``tools`` — declarative tool catalog.
     - one row per named tool
     - ``harness_type`` namespaces the tool to a concrete adapter
     - ``permission_class`` lets the product layer apply per-session policy
     - ``input_schema_json`` is an OpenAI-compatible function-calling schema
2. New column on ``harness_runs`` — ``tool_invocation_id``
     - non-null when a harness run was triggered by a tool call from an agent
     - used by the forensic UI to link a tool bubble back to its harness run

All changes are additive: existing tables keep their original columns and
constraints.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # -----------------------------------------------------------------------
    # tools: declarative catalog
    # -----------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tools (
            tool_id              TEXT PRIMARY KEY,
            name                 TEXT NOT NULL,
            description          TEXT NOT NULL DEFAULT '',
            harness_type         TEXT NOT NULL
                                 CHECK (harness_type IN ('shell', 'ssh', 'opencode', 'hermes', 'discussion')),
            adapter_method       TEXT NOT NULL,
            input_schema_json    TEXT NOT NULL DEFAULT '{}',
            permission_class     TEXT NOT NULL DEFAULT 'read_only'
                                 CHECK (permission_class IN (
                                     'read_only', 'write_local',
                                     'write_remote', 'destructive'
                                 )),
            is_enabled           INTEGER NOT NULL DEFAULT 1
                                 CHECK (is_enabled IN (0, 1)),
            is_builtin           INTEGER NOT NULL DEFAULT 0
                                 CHECK (is_builtin IN (0, 1)),
            created_at           REAL NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at           REAL NOT NULL DEFAULT (strftime('%s', 'now')),
            UNIQUE (harness_type, name)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tools_harness "
        "ON tools(harness_type, is_enabled)"
    )

    # -----------------------------------------------------------------------
    # harness_runs: backlink to the tool invocation that triggered it
    # -----------------------------------------------------------------------
    existing_cols = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute("PRAGMA table_info(harness_runs)").fetchall()
    }
    if "tool_invocation_id" not in existing_cols:
        conn.execute(
            "ALTER TABLE harness_runs ADD COLUMN tool_invocation_id TEXT"
        )

    # -----------------------------------------------------------------------
    # tool_invocations: per-call record
    # -----------------------------------------------------------------------
    # One row per tool_call that came back from a provider.  Links to the
    # harness_runs row the dispatcher created.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_invocations (
            invocation_id    TEXT PRIMARY KEY,
            session_id       TEXT NOT NULL,
            workspace_id     TEXT NOT NULL,
            tool_id          TEXT NOT NULL REFERENCES tools(tool_id),
            tool_name        TEXT NOT NULL,
            arguments_json   TEXT NOT NULL DEFAULT '{}',
            status           TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN (
                                 'pending', 'running', 'completed',
                                 'failed', 'denied'
                             )),
            result_text      TEXT,
            error_text       TEXT,
            harness_run_id   TEXT REFERENCES harness_runs(harness_run_id),
            created_at       REAL NOT NULL DEFAULT (strftime('%s', 'now')),
            completed_at     REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_invocations_session "
        "ON tool_invocations(session_id, created_at)"
    )
