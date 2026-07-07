"""Shared test fixtures for Agent Workbench."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from agent_workbench.db import get_connection, apply_migrations


@pytest.fixture
def tmp_db(tmp_path: Path):
    """Return a path to a temporary SQLite database file."""
    return tmp_path / "test.db"


@pytest.fixture
def db(tmp_db: Path):
    """Return a connection to a freshly created, migrated database."""
    conn = get_connection(str(tmp_db))
    apply_migrations(conn)
    # Seed builtin tools so tests that exercise the tool registry or
    # dispatcher don't need to do it themselves.
    from agent_workbench.services.tool_seeds import seed_builtin_tools
    seed_builtin_tools(conn)
    yield conn
    conn.close()
