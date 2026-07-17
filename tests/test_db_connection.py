"""Tests for db/connection — connection creation, WAL mode, busy timeout."""

from __future__ import annotations

import sqlite3
from pathlib import Path


from agent_workbench.db.connection import get_connection


class TestGetConnection:
    def test_creates_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "fresh.db"
        assert not db_path.exists()
        conn = get_connection(db_path)
        assert db_path.exists()
        conn.close()

    def test_wal_mode(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "wal.db")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal", f"Expected WAL mode, got {mode!r}"
        conn.close()

    def test_busy_timeout(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "busy.db", busy_timeout_ms=7_500)
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 7_500, f"Expected 7500, got {timeout}"
        conn.close()

    def test_foreign_keys_enabled(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "fk.db")
        enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert enabled == 1, f"Foreign keys not enabled (got {enabled})"
        conn.close()

    def test_row_factory(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "rf.db")
        assert conn.row_factory is sqlite3.Row
        conn.close()

    def test_default_busy_timeout(self, tmp_path: Path) -> None:
        conn = get_connection(tmp_path / "default.db")
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5_000

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c" / "nested.db"
        assert not nested.parent.exists()
        conn = get_connection(nested)
        assert nested.parent.exists()
        conn.close()
