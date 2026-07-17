"""011 — Alpha persistence: session labels, users, project assets, participant transfers.

Adds four new tables plus backfill for the architecture's persistence/domain lane:

1. ``session_labels`` — extensible label catalog (chat/research/work are builtins).
2. ``users`` — local identity records for session-backed user cookies.
3. ``project_assets`` — workspace-scoped directory/repository/file links.
4. ``participant_transfers`` — metadata for participant transfer operations.

All changes are additive: existing tables keep their original columns and
constraints.
"""

from __future__ import annotations

import sqlite3

_BUILTIN_LABELS = (
    ("chat", "Chat", "#4A90D9", "General conversation and discussion."),
    ("research", "Research", "#7B61FF", "Evidence gathering and analysis."),
    ("work", "Work", "#E8922E", "Structured task execution."),
)


def up(conn: sqlite3.Connection) -> None:
    # -------------------------------------------------------------------
    # 1. session_labels — extensible label catalog
    # -------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_labels (
            label_id       TEXT PRIMARY KEY,
            workspace_id   TEXT NOT NULL REFERENCES workspaces(workspace_id),
            name           TEXT NOT NULL,
            display_name   TEXT NOT NULL,
            color          TEXT NOT NULL DEFAULT '#4A90D9',
            description    TEXT NOT NULL DEFAULT '',
            is_builtin     INTEGER NOT NULL DEFAULT 0
                           CHECK (is_builtin IN (0, 1)),
            created_at     REAL NOT NULL,
            UNIQUE (workspace_id, name)
        )
        """
    )

    # -------------------------------------------------------------------
    # 2. users — local identity records
    # -------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id       TEXT PRIMARY KEY,
            display_name  TEXT NOT NULL DEFAULT '',
            created_at    REAL NOT NULL,
            last_seen_at  REAL NOT NULL
        )
        """
    )

    # -------------------------------------------------------------------
    # 3. project_assets — workspace-scoped directory/repository/file links
    # -------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_assets (
            asset_id       TEXT PRIMARY KEY,
            workspace_id   TEXT NOT NULL REFERENCES workspaces(workspace_id),
            asset_type     TEXT NOT NULL
                           CHECK (asset_type IN ('directory', 'repository', 'file')),
            path           TEXT NOT NULL,
            label          TEXT NOT NULL DEFAULT '',
            description    TEXT NOT NULL DEFAULT '',
            session_id     TEXT,
            agent_id       TEXT,
            created_at     REAL NOT NULL,
            updated_at     REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_assets_workspace "
        "ON project_assets(workspace_id, asset_type)"
    )

    # -------------------------------------------------------------------
    # 4. participant_transfers — metadata for transfer operations
    # -------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS participant_transfers (
            transfer_id            TEXT PRIMARY KEY,
            source_session_id      TEXT NOT NULL,
            target_session_id      TEXT NOT NULL,
            initiated_by           TEXT NOT NULL DEFAULT 'user'
                                   CHECK (initiated_by IN ('user', 'orchestrator', 'system')),
            transferred_participants_json TEXT NOT NULL DEFAULT '[]',
            context_summary        TEXT NOT NULL DEFAULT '',
            status                 TEXT NOT NULL DEFAULT 'pending'
                                   CHECK (status IN (
                                       'pending', 'completed', 'failed', 'cancelled'
                                   )),
            created_at             REAL NOT NULL,
            completed_at           REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_participant_transfers_source "
        "ON participant_transfers(source_session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_participant_transfers_target "
        "ON participant_transfers(target_session_id)"
    )

    conn.commit()