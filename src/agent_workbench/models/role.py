"""Role domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Role:
    role_id: str
    name: str
    description: str
    system_prompt: str
    is_builtin: bool
    created_at: float
    updated_at: float


class RoleRepository:
    """SQLite-backed repository for Role entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        name: str,
        description: str = "",
        system_prompt: str = "",
        is_builtin: bool = False,
    ) -> Role:
        role_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO roles "
            "(role_id, name, description, system_prompt, is_builtin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                role_id,
                name,
                description,
                system_prompt,
                1 if is_builtin else 0,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get_by_id(role_id)  # type: ignore[return-value]

    def get_by_id(self, role_id: str) -> Optional[Role]:
        row = self.conn.execute(
            "SELECT role_id, name, description, system_prompt, is_builtin, created_at, updated_at "
            "FROM roles WHERE role_id = ?",
            (role_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_all(self) -> List[Role]:
        rows = self.conn.execute(
            "SELECT role_id, name, description, system_prompt, is_builtin, created_at, updated_at "
            "FROM roles ORDER BY is_builtin DESC, name ASC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def update(
        self,
        role_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Optional[Role]:
        existing = self.get_by_id(role_id)
        if existing is None:
            return None
        now = time.time()
        self.conn.execute(
            "UPDATE roles SET name = ?, description = ?, system_prompt = ?, updated_at = ? "
            "WHERE role_id = ?",
            (
                name if name is not None else existing.name,
                description if description is not None else existing.description,
                system_prompt if system_prompt is not None else existing.system_prompt,
                now,
                role_id,
            ),
        )
        self.conn.commit()
        return self.get_by_id(role_id)

    def delete(self, role_id: str) -> bool:
        cursor = self.conn.execute("DELETE FROM roles WHERE role_id = ?", (role_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> Role:
        return Role(
            role_id=row["role_id"],
            name=row["name"],
            description=row["description"],
            system_prompt=row["system_prompt"],
            is_builtin=bool(row["is_builtin"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
