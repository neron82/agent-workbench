"""Workspace domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Workspace:
    workspace_id: str
    tenant_id: str
    name: str
    is_default: bool
    created_at: float


class WorkspaceRepository:
    """SQLite-backed repository for Workspace entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        tenant_id: str,
        name: str,
        is_default: bool = False,
    ) -> Workspace:
        """Insert a new workspace and return the persisted instance."""
        workspace_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO workspaces (workspace_id, tenant_id, name, is_default, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (workspace_id, tenant_id, name, int(is_default), created_at),
        )
        self.conn.commit()
        return Workspace(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            name=name,
            is_default=is_default,
            created_at=created_at,
        )

    def get_by_id(self, workspace_id: str) -> Optional[Workspace]:
        row = self.conn.execute(
            "SELECT workspace_id, tenant_id, name, is_default, created_at "
            "FROM workspaces WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def get_default(self, tenant_id: str) -> Optional[Workspace]:
        row = self.conn.execute(
            "SELECT workspace_id, tenant_id, name, is_default, created_at "
            "FROM workspaces WHERE tenant_id = ? AND is_default = 1 "
            "LIMIT 1",
            (tenant_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_all(self) -> List[Workspace]:
        rows = self.conn.execute(
            "SELECT workspace_id, tenant_id, name, is_default, created_at "
            "FROM workspaces ORDER BY created_at DESC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def update(
        self,
        workspace_id: str,
        *,
        name: Optional[str] = None,
        is_default: Optional[bool] = None,
    ) -> Optional[Workspace]:
        """Update mutable fields on a workspace. Returns the updated instance or None."""
        updates: list[str] = []
        params: list = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if is_default is not None:
            updates.append("is_default = ?")
            params.append(int(is_default))

        if not updates:
            return self.get_by_id(workspace_id)

        params.append(workspace_id)
        self.conn.execute(
            f"UPDATE workspaces SET {', '.join(updates)} WHERE workspace_id = ?",
            params,
        )
        self.conn.commit()
        return self.get_by_id(workspace_id)

    def delete(self, workspace_id: str) -> bool:
        """Delete a workspace. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM workspaces WHERE workspace_id = ?",
            (workspace_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> Workspace:
        return Workspace(
            workspace_id=row["workspace_id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            is_default=bool(row["is_default"]),
            created_at=row["created_at"],
        )
