"""Project asset domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


ASSET_TYPES = ("directory", "repository", "file")


@dataclass
class ProjectAsset:
    asset_id: str
    workspace_id: str
    asset_type: str
    path: str
    label: str
    description: str
    session_id: Optional[str]
    agent_id: Optional[str]
    created_at: float
    updated_at: float


class ProjectAssetRepository:
    """SQLite-backed repository for project-scoped assets."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        workspace_id: str,
        asset_type: str,
        path: str,
        label: str = "",
        description: str = "",
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> ProjectAsset:
        if asset_type not in ASSET_TYPES:
            raise ValueError(
                f"Invalid asset_type: {asset_type!r}. Must be one of {ASSET_TYPES}"
            )
        asset_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO project_assets "
            "(asset_id, workspace_id, asset_type, path, label, description, session_id, agent_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, workspace_id, asset_type, path, label, description, session_id, agent_id, now, now),
        )
        self.conn.commit()
        return self.get_by_id(asset_id)  # type: ignore[return-value]

    def get_by_id(self, asset_id: str) -> Optional[ProjectAsset]:
        row = self.conn.execute(
            "SELECT asset_id, workspace_id, asset_type, path, label, description, "
            "session_id, agent_id, created_at, updated_at "
            "FROM project_assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_workspace(
        self, workspace_id: str, asset_type: Optional[str] = None
    ) -> List[ProjectAsset]:
        if asset_type is not None:
            rows = self.conn.execute(
                "SELECT asset_id, workspace_id, asset_type, path, label, description, "
                "session_id, agent_id, created_at, updated_at "
                "FROM project_assets WHERE workspace_id = ? AND asset_type = ? ORDER BY label ASC",
                (workspace_id, asset_type),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT asset_id, workspace_id, asset_type, path, label, description, "
                "session_id, agent_id, created_at, updated_at "
                "FROM project_assets WHERE workspace_id = ? ORDER BY asset_type, label ASC",
                (workspace_id,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def list_by_session(self, session_id: str) -> List[ProjectAsset]:
        rows = self.conn.execute(
            "SELECT asset_id, workspace_id, asset_type, path, label, description, "
            "session_id, agent_id, created_at, updated_at "
            "FROM project_assets WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update(
        self,
        asset_id: str,
        *,
        label: Optional[str] = None,
        description: Optional[str] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[ProjectAsset]:
        parts: list[str] = ["updated_at = ?"]
        params: list = [time.time()]
        if label is not None:
            parts.append("label = ?")
            params.append(label)
        if description is not None:
            parts.append("description = ?")
            params.append(description)
        # Use sentinel for "clear to None" vs "leave unchanged"
        if session_id is not None:
            parts.append("session_id = ?")
            params.append(session_id)
        if agent_id is not None:
            parts.append("agent_id = ?")
            params.append(agent_id)
        params.append(asset_id)
        self.conn.execute(
            f"UPDATE project_assets SET {', '.join(parts)} WHERE asset_id = ?",
            params,
        )
        self.conn.commit()
        return self.get_by_id(asset_id)

    def delete(self, asset_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM project_assets WHERE asset_id = ?",
            (asset_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> ProjectAsset:
        return ProjectAsset(
            asset_id=row["asset_id"],
            workspace_id=row["workspace_id"],
            asset_type=row["asset_type"],
            path=row["path"],
            label=row["label"],
            description=row["description"],
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )