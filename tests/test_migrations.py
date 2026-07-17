"""Tests for db/migrations — framework lifecycle, tracking, idempotency."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_workbench.db.connection import get_connection
from agent_workbench.db.migration_framework import apply_migrations
from agent_workbench.models.workspace import WorkspaceRepository


class TestMigrations:
    def test_applies_cleanly(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "m.db")
        applied = apply_migrations(conn)
        assert "001_initial_schema" in applied
        assert "002_chat_ui_foundations" in applied
        assert "011_alpha_persistence" in applied
        assert "012_repair_session_channels" in applied
        conn.close()

    def test_tracks_in_migrations_table(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "track.db")
        apply_migrations(conn)
        rows = conn.execute("SELECT name FROM _migrations ORDER BY name").fetchall()
        names = {r["name"] for r in rows}
        assert "001_initial_schema" in names
        assert "002_chat_ui_foundations" in names
        conn.close()

    def test_foreign_keys_remain_enabled_after_migrations(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "fk.db")
        apply_migrations(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        workspace = WorkspaceRepository(conn).create(tenant_id="t1", name="fk-test")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO session_extensions "
                "(session_id, workspace_id, session_type, fork_id) "
                "VALUES (?, ?, 'chat', 'missing-fork')",
                ("invalid-fork-session", workspace.workspace_id),
            )
        conn.close()

    def test_idempotent(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "idem.db")
        first = apply_migrations(conn)
        assert "001_initial_schema" in first
        second = apply_migrations(conn)
        assert "001_initial_schema" not in second
        assert len(second) == 0
        conn.close()

    def test_migrations_table_created_before_any_migration(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "table.db")
        apply_migrations(conn)
        # Verify the table exists
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
        ).fetchall()
        assert len(tables) == 1
        conn.close()

    def test_applied_at_timestamp(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "ts.db")
        apply_migrations(conn)
        row = conn.execute("SELECT applied_at FROM _migrations WHERE name='001_initial_schema'").fetchone()
        assert row is not None
        assert row["applied_at"] is not None
        assert isinstance(row["applied_at"], float)
        row2 = conn.execute("SELECT applied_at FROM _migrations WHERE name='002_chat_ui_foundations'").fetchone()
        assert row2 is not None
        assert isinstance(row2["applied_at"], float)
        conn.close()
