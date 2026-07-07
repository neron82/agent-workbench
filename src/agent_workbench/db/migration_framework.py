"""Simple migration framework for Agent Workbench.

Each migration is a Python module inside ``src/agent_workbench/db/migrations/``
whose filename starts with a numeric prefix (e.g. ``001_initial_schema.py``).
The module must expose an ``up(conn)`` callable that receives a
``sqlite3.Connection`` and applies the forward migration.

Migrations are applied in filename order.  Applied migration names are tracked
in a ``_migrations`` table so that repeated calls are idempotent.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sqlite3
from pathlib import Path

import agent_workbench.db.migrations as _migrations_pkg

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = Path(_migrations_pkg.__file__).resolve().parent


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Create the ``_migrations`` tracking table if it doesn't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            name        TEXT PRIMARY KEY,
            applied_at  REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        )
        """
    )
    conn.commit()


def _applied_migration_names(conn: sqlite3.Connection) -> set[str]:
    _ensure_migrations_table(conn)
    rows = conn.execute("SELECT name FROM _migrations").fetchall()
    return {row["name"] for row in rows}


def _load_migration(name: str) -> callable:
    """Dynamically import a migration module and return its ``up`` function."""
    module_name = f"agent_workbench.db.migrations.{name}"
    mod = importlib.import_module(module_name)
    if not hasattr(mod, "up"):
        raise ValueError(
            f"Migration module {module_name!r} does not define an 'up' callable"
        )
    return mod.up


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """Apply any pending migrations to *conn*.

    Returns the list of migration names that were actually applied (may be
    empty if all migrations are already up-to-date).
    """
    _ensure_migrations_table(conn)
    applied = _applied_migration_names(conn)
    pending = _discover_pending(_MIGRATIONS_DIR, applied)

    results: list[str] = []
    for name in pending:
        up_fn = _load_migration(name)
        up_fn(conn)
        conn.execute(
            "INSERT INTO _migrations (name) VALUES (?)", (name,)
        )
        conn.commit()
        results.append(name)

    return results


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _discover_pending(migrations_dir: Path, applied: set[str]) -> list[str]:
    """Return migration module names (without ``.py``) that haven't been applied yet.

    Scans for ``<digits>_<name>.py`` files in *migrations_dir* and returns
    them sorted by filename.
    """
    pending: list[str] = []
    for entry in sorted(migrations_dir.iterdir()):
        if not entry.is_file() or entry.name.startswith("_"):
            continue
        if not entry.name.endswith(".py"):
            continue
        mod_name = entry.stem  # e.g. "001_initial_schema"
        if mod_name not in applied:
            pending.append(mod_name)
    return pending
