"""SQLite connection management for Agent Workbench.

Provides factory functions for opening the product-layer database
(`workbench.db`) with WAL mode, busy timeout, and foreign-key enforcement.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "workbench.db"
_DEFAULT_BUSY_TIMEOUT_MS = 5_000  # 5 seconds


def get_connection(
    db_path: str | Path | None = None,
    *,
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Open (or create) a SQLite database and return a configured connection.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file. Defaults to ``workbench.db`` next to
        the project root.
    busy_timeout_ms:
        SQLite busy timeout in milliseconds.  Controls how long a connection
        waits when a table is locked before raising ``OperationalError``.

    Returns
    -------
    sqlite3.Connection
        A connection with WAL mode, foreign keys enabled, and the requested
        busy timeout.
    """
    if db_path is None:
        db_path = _DEFAULT_DB_PATH
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def open_workbench(
    db_path: Optional[str | Path] = None,
    *,
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Alias for :func:`get_connection` — kept for API ergonomics.

    >>> conn = open_workbench("/path/to/workbench.db")
    """
    return get_connection(db_path, busy_timeout_ms=busy_timeout_ms)
