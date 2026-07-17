"""013 — Beta teams and integrity repair.

Adds two new tables for workspace-scoped reusable agent teams:

1. ``agent_teams`` — named groups of agent profiles scoped to a workspace.
2. ``agent_team_members`` — ordered membership with role labels, cascade-deletes
   with the parent team.

Also repairs existing historical integrity damage:

- Deletes ``session_participants`` rows whose ``binding_id`` IS NULL AND
  ``removed_at`` IS NOT NULL (orphan removed participants).
- Raises ``RuntimeError`` if any ``session_participants`` row has a NULL
  ``binding_id`` AND a NULL ``removed_at`` (active orphan) — the caller must
  resolve these manually before the migration can complete.
- Removes ``participant_transfers`` whose ``source_session_id`` or
  ``target_session_id`` no longer exists in ``session_extensions``.
- Nulls ``project_assets.session_id`` where the referenced session is missing.

All operations are idempotent.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # ------------------------------------------------------------------
    # 1. agent_teams — workspace-scoped reusable team definitions
    # ------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_teams (
            team_id       TEXT PRIMARY KEY,
            workspace_id  TEXT NOT NULL REFERENCES workspaces(workspace_id),
            name          TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL,
            UNIQUE (workspace_id, name)
        )
        """
    )

    # ------------------------------------------------------------------
    # 2. agent_team_members — ordered membership with cascade delete
    # ------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_team_members (
            member_id        TEXT PRIMARY KEY,
            team_id          TEXT NOT NULL REFERENCES agent_teams(team_id) ON DELETE CASCADE,
            agent_profile_id TEXT NOT NULL REFERENCES agent_profiles(agent_profile_id),
            role_label       TEXT NOT NULL DEFAULT '',
            sort_order       INTEGER NOT NULL DEFAULT 0,
            created_at       REAL NOT NULL,
            UNIQUE (team_id, agent_profile_id)
        )
        """
    )

    # ------------------------------------------------------------------
    # 3. Integrity repair — session_participants
    # ------------------------------------------------------------------
    # 3a. Delete orphan removed participants: binding_id IS NULL AND removed_at IS NOT NULL
    conn.execute(
        """
        DELETE FROM session_participants
        WHERE binding_id IS NULL
          AND removed_at IS NOT NULL
        """
    )

    # 3b. Fail on active orphans: binding_id IS NULL AND removed_at IS NULL
    active_orphans = conn.execute(
        """
        SELECT COUNT(*) FROM session_participants
        WHERE binding_id IS NULL
          AND removed_at IS NULL
        """
    ).fetchone()[0]

    if active_orphans > 0:
        raise RuntimeError(
            f"Migration 013: {active_orphans} active orphan session_participants "
            f"found (binding_id IS NULL AND removed_at IS NULL). "
            f"Resolve these manually before re-running the migration."
        )

    # ------------------------------------------------------------------
    # 4. Integrity repair — participant_transfers
    # ------------------------------------------------------------------
    # Remove transfers whose source_session_id no longer exists
    conn.execute(
        """
        DELETE FROM participant_transfers
        WHERE source_session_id NOT IN (
            SELECT session_id FROM session_extensions
        )
        """
    )
    # Remove transfers whose target_session_id no longer exists
    conn.execute(
        """
        DELETE FROM participant_transfers
        WHERE target_session_id NOT IN (
            SELECT session_id FROM session_extensions
        )
        """
    )

    # ------------------------------------------------------------------
    # 5. Integrity repair — project_assets
    # ------------------------------------------------------------------
    # Null session_id where the referenced session is missing
    conn.execute(
        """
        UPDATE project_assets
        SET session_id = NULL
        WHERE session_id IS NOT NULL
          AND session_id NOT IN (
              SELECT session_id FROM session_extensions
          )
        """
    )

    conn.commit()
