"""Tool domain model and repository.

A Tool is a declarative record that maps a (harness_type, name) to a
concrete adapter method, plus the JSON-schema description sent to the
provider's tool-calling endpoint.

This is *not* a dynamic plugin registry.  For MVP the catalog is
populated by builtin rows seeded at startup; user-defined tools can be
added later through the same API.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


PERMISSION_CLASSES = (
    "read_only", "write_local", "write_remote", "destructive",
)

HARNESS_TYPES = ("shell", "ssh", "opencode", "hermes", "discussion")


@dataclass
class Tool:
    tool_id: str
    name: str
    description: str
    harness_type: str
    adapter_method: str
    input_schema_json: Dict[str, Any]
    permission_class: str
    is_enabled: bool
    is_builtin: bool
    created_at: float
    updated_at: float


class ToolRepository:
    """SQLite-backed repository for Tool entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        harness_type: str,
        adapter_method: str,
        description: str = "",
        input_schema: Optional[Dict[str, Any]] = None,
        permission_class: str = "read_only",
        is_enabled: bool = True,
        is_builtin: bool = False,
    ) -> Tool:
        if harness_type not in HARNESS_TYPES:
            raise ValueError(
                f"Invalid harness_type: {harness_type!r}. "
                f"Must be one of {HARNESS_TYPES}"
            )
        if permission_class not in PERMISSION_CLASSES:
            raise ValueError(
                f"Invalid permission_class: {permission_class!r}. "
                f"Must be one of {PERMISSION_CLASSES}"
            )
        tool_id = uuid.uuid4().hex
        now = time.time()
        schema = input_schema or {
            "type": "object",
            "properties": {},
            "required": [],
        }
        try:
            self.conn.execute(
                "INSERT INTO tools "
                "(tool_id, name, description, harness_type, adapter_method, "
                "input_schema_json, permission_class, is_enabled, is_builtin, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tool_id,
                    name,
                    description,
                    harness_type,
                    adapter_method,
                    json.dumps(schema, sort_keys=True),
                    permission_class,
                    1 if is_enabled else 0,
                    1 if is_builtin else 0,
                    now,
                    now,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Tool ({harness_type!r}, {name!r}) already exists"
            ) from exc
        return self.get_by_id(tool_id)  # type: ignore[return-value]

    def get_by_id(self, tool_id: str) -> Optional[Tool]:
        row = self.conn.execute(
            "SELECT tool_id, name, description, harness_type, adapter_method, "
            "input_schema_json, permission_class, is_enabled, is_builtin, "
            "created_at, updated_at FROM tools WHERE tool_id = ?",
            (tool_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def get_by_name(self, harness_type: str, name: str) -> Optional[Tool]:
        row = self.conn.execute(
            "SELECT tool_id, name, description, harness_type, adapter_method, "
            "input_schema_json, permission_class, is_enabled, is_builtin, "
            "created_at, updated_at FROM tools "
            "WHERE harness_type = ? AND name = ?",
            (harness_type, name),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_enabled(self) -> List[Tool]:
        rows = self.conn.execute(
            "SELECT tool_id, name, description, harness_type, adapter_method, "
            "input_schema_json, permission_class, is_enabled, is_builtin, "
            "created_at, updated_at FROM tools "
            "WHERE is_enabled = 1 ORDER BY harness_type ASC, name ASC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_all(self) -> List[Tool]:
        rows = self.conn.execute(
            "SELECT tool_id, name, description, harness_type, adapter_method, "
            "input_schema_json, permission_class, is_enabled, is_builtin, "
            "created_at, updated_at FROM tools "
            "ORDER BY harness_type ASC, name ASC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_for_harness(self, harness_type: str) -> List[Tool]:
        rows = self.conn.execute(
            "SELECT tool_id, name, description, harness_type, adapter_method, "
            "input_schema_json, permission_class, is_enabled, is_builtin, "
            "created_at, updated_at FROM tools "
            "WHERE harness_type = ? AND is_enabled = 1 ORDER BY name ASC",
            (harness_type,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update(
        self,
        tool_id: str,
        *,
        description: Optional[str] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        permission_class: Optional[str] = None,
        is_enabled: Optional[bool] = None,
    ) -> Optional[Tool]:
        existing = self.get_by_id(tool_id)
        if existing is None:
            return None
        if permission_class is not None and permission_class not in PERMISSION_CLASSES:
            raise ValueError(
                f"Invalid permission_class: {permission_class!r}"
            )
        updates: list[str] = []
        params: list = []
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if input_schema is not None:
            updates.append("input_schema_json = ?")
            params.append(json.dumps(input_schema, sort_keys=True))
        if permission_class is not None:
            updates.append("permission_class = ?")
            params.append(permission_class)
        if is_enabled is not None:
            updates.append("is_enabled = ?")
            params.append(1 if is_enabled else 0)
        if not updates:
            return existing
        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(tool_id)
        self.conn.execute(
            f"UPDATE tools SET {', '.join(updates)} WHERE tool_id = ?",
            params,
        )
        self.conn.commit()
        return self.get_by_id(tool_id)

    def delete(self, tool_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM tools WHERE tool_id = ? AND is_builtin = 0",
            (tool_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> Tool:
        raw = row["input_schema_json"]
        try:
            schema = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            schema = {}
        return Tool(
            tool_id=row["tool_id"],
            name=row["name"],
            description=row["description"],
            harness_type=row["harness_type"],
            adapter_method=row["adapter_method"],
            input_schema_json=schema,
            permission_class=row["permission_class"],
            is_enabled=bool(row["is_enabled"]),
            is_builtin=bool(row["is_builtin"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
