"""Session label domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SessionLabel:
    label_id: str
    workspace_id: str
    name: str
    display_name: str
    color: str
    description: str
    is_builtin: bool
    created_at: float


class SessionLabelRepository:
    """SQLite-backed repository for session labels."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        workspace_id: str,
        name: str,
        display_name: str = "",
        color: str = "#4A90D9",
        description: str = "",
    ) -> SessionLabel:
        label_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO session_labels "
            "(label_id, workspace_id, name, display_name, color, description, is_builtin, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (label_id, workspace_id, name, display_name, color, description, created_at),
        )
        self.conn.commit()
        return self.get_by_id(label_id)  # type: ignore[return-value]

    def get_by_id(self, label_id: str) -> Optional[SessionLabel]:
        row = self.conn.execute(
            "SELECT label_id, workspace_id, name, display_name, color, description, is_builtin, created_at "
            "FROM session_labels WHERE label_id = ?",
            (label_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def get_by_name(self, workspace_id: str, name: str) -> Optional[SessionLabel]:
        row = self.conn.execute(
            "SELECT label_id, workspace_id, name, display_name, color, description, is_builtin, created_at "
            "FROM session_labels WHERE workspace_id = ? AND name = ?",
            (workspace_id, name),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_workspace(self, workspace_id: str) -> List[SessionLabel]:
        rows = self.conn.execute(
            "SELECT label_id, workspace_id, name, display_name, color, description, is_builtin, created_at "
            "FROM session_labels WHERE workspace_id = ? ORDER BY name ASC",
            (workspace_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update(
        self,
        label_id: str,
        *,
        display_name: Optional[str] = None,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[SessionLabel]:
        parts: list[str] = []
        params: list = []
        if display_name is not None:
            parts.append("display_name = ?")
            params.append(display_name)
        if color is not None:
            parts.append("color = ?")
            params.append(color)
        if description is not None:
            parts.append("description = ?")
            params.append(description)
        if not parts:
            return self.get_by_id(label_id)
        params.append(label_id)
        self.conn.execute(
            f"UPDATE session_labels SET {', '.join(parts)} WHERE label_id = ?",
            params,
        )
        self.conn.commit()
        return self.get_by_id(label_id)

    def delete(self, label_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM session_labels WHERE label_id = ? AND is_builtin = 0",
            (label_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> SessionLabel:
        return SessionLabel(
            label_id=row["label_id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            display_name=row["display_name"],
            color=row["color"],
            description=row["description"],
            is_builtin=bool(row["is_builtin"]),
            created_at=row["created_at"],
        )