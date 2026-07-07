"""TaskSpec domain model and repository."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskSpec:
    task_spec_id: str
    workspace_id: str
    source_session_id: Optional[str]
    objective: str
    scope_in: Optional[Dict[str, Any]]
    scope_out: Optional[Dict[str, Any]]
    acceptance_criteria: Optional[Dict[str, Any]]
    constraints: Optional[Dict[str, Any]]
    risk_level: Optional[str]
    approval_status: str
    created_at: float
    updated_at: float


class TaskSpecRepository:
    """SQLite-backed repository for TaskSpec entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        workspace_id: str,
        source_session_id: Optional[str] = None,
        objective: str = "",
        scope_in: Optional[Dict[str, Any]] = None,
        scope_out: Optional[Dict[str, Any]] = None,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
        constraints: Optional[Dict[str, Any]] = None,
        risk_level: Optional[str] = None,
        approval_status: str = "draft",
    ) -> TaskSpec:
        """Insert a new task spec and return the persisted instance."""
        task_spec_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO task_specs "
            "(task_spec_id, workspace_id, source_session_id, objective, "
            "scope_in_json, scope_out_json, acceptance_criteria_json, "
            "constraints_json, risk_level, approval_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_spec_id,
                workspace_id,
                source_session_id,
                objective,
                json.dumps(scope_in) if scope_in is not None else None,
                json.dumps(scope_out) if scope_out is not None else None,
                json.dumps(acceptance_criteria) if acceptance_criteria is not None else None,
                json.dumps(constraints) if constraints is not None else None,
                risk_level,
                approval_status,
                now,
                now,
            ),
        )
        self.conn.commit()
        return TaskSpec(
            task_spec_id=task_spec_id,
            workspace_id=workspace_id,
            source_session_id=source_session_id,
            objective=objective,
            scope_in=scope_in,
            scope_out=scope_out,
            acceptance_criteria=acceptance_criteria,
            constraints=constraints,
            risk_level=risk_level,
            approval_status=approval_status,
            created_at=now,
            updated_at=now,
        )

    def get_by_id(self, task_spec_id: str) -> Optional[TaskSpec]:
        row = self.conn.execute(
            "SELECT task_spec_id, workspace_id, source_session_id, objective, "
            "scope_in_json, scope_out_json, acceptance_criteria_json, "
            "constraints_json, risk_level, approval_status, created_at, updated_at "
            "FROM task_specs WHERE task_spec_id = ?",
            (task_spec_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_workspace(self, workspace_id: str) -> List[TaskSpec]:
        rows = self.conn.execute(
            "SELECT task_spec_id, workspace_id, source_session_id, objective, "
            "scope_in_json, scope_out_json, acceptance_criteria_json, "
            "constraints_json, risk_level, approval_status, created_at, updated_at "
            "FROM task_specs WHERE workspace_id = ? ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update_approval_status(
        self, task_spec_id: str, *, approval_status: str
    ) -> Optional[TaskSpec]:
        """Update the approval_status of a task spec. Returns the updated instance or None."""
        now = time.time()
        cursor = self.conn.execute(
            "UPDATE task_specs SET approval_status = ?, updated_at = ? "
            "WHERE task_spec_id = ?",
            (approval_status, now, task_spec_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(task_spec_id)

    def update(
        self,
        task_spec_id: str,
        *,
        objective: Optional[str] = None,
        scope_in: Optional[Dict[str, Any]] = None,
        scope_out: Optional[Dict[str, Any]] = None,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
        constraints: Optional[Dict[str, Any]] = None,
        risk_level: Optional[str] = None,
        approval_status: Optional[str] = None,
    ) -> Optional[TaskSpec]:
        """Update mutable fields on a task spec. Returns the updated instance or None."""
        updates: list[str] = []
        params: list = []

        if objective is not None:
            updates.append("objective = ?")
            params.append(objective)
        if scope_in is not None:
            updates.append("scope_in_json = ?")
            params.append(json.dumps(scope_in))
        if scope_out is not None:
            updates.append("scope_out_json = ?")
            params.append(json.dumps(scope_out))
        if acceptance_criteria is not None:
            updates.append("acceptance_criteria_json = ?")
            params.append(json.dumps(acceptance_criteria))
        if constraints is not None:
            updates.append("constraints_json = ?")
            params.append(json.dumps(constraints))
        if risk_level is not None:
            updates.append("risk_level = ?")
            params.append(risk_level)
        if approval_status is not None:
            updates.append("approval_status = ?")
            params.append(approval_status)

        if not updates:
            return self.get_by_id(task_spec_id)

        now = time.time()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(task_spec_id)

        self.conn.execute(
            f"UPDATE task_specs SET {', '.join(updates)} WHERE task_spec_id = ?",
            params,
        )
        self.conn.commit()
        return self.get_by_id(task_spec_id)

    def delete(self, task_spec_id: str) -> bool:
        """Delete a task spec. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM task_specs WHERE task_spec_id = ?",
            (task_spec_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> TaskSpec:
        return TaskSpec(
            task_spec_id=row["task_spec_id"],
            workspace_id=row["workspace_id"],
            source_session_id=row["source_session_id"],
            objective=row["objective"],
            scope_in=json.loads(row["scope_in_json"]) if row["scope_in_json"] else None,
            scope_out=json.loads(row["scope_out_json"]) if row["scope_out_json"] else None,
            acceptance_criteria=json.loads(row["acceptance_criteria_json"]) if row["acceptance_criteria_json"] else None,
            constraints=json.loads(row["constraints_json"]) if row["constraints_json"] else None,
            risk_level=row["risk_level"],
            approval_status=row["approval_status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
