"""Harness run domain model and repository."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class HarnessRun:
    harness_run_id: str
    workspace_id: str
    session_id: str
    task_spec_id: Optional[str]
    harness_type: str
    runtime_session_id: Optional[str]
    runtime_process_id: Optional[str]
    runtime_remote_process_id: Optional[str]
    status: str
    control_capabilities_json: Optional[str]
    artifact_summary_json: Optional[str]
    started_at: Optional[float]
    ended_at: Optional[float]
    pgid: Optional[str] = None
    exit_code: Optional[int] = None
    exit_signal: Optional[int] = None
    tool_invocation_id: Optional[str] = None


class HarnessRunRepository:
    """SQLite-backed repository for HarnessRun entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        workspace_id: str,
        session_id: str,
        harness_type: str,
        task_spec_id: Optional[str] = None,
        status: str = "queued",
        control_capabilities: Optional[Dict[str, Any]] = None,
        artifact_summary: Optional[Dict[str, Any]] = None,
    ) -> HarnessRun:
        """Insert a new harness run and return the persisted instance."""
        harness_run_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO harness_runs "
            "(harness_run_id, workspace_id, session_id, task_spec_id, "
            "harness_type, status, control_capabilities_json, artifact_summary_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                harness_run_id,
                workspace_id,
                session_id,
                task_spec_id,
                harness_type,
                status,
                json.dumps(control_capabilities) if control_capabilities else None,
                json.dumps(artifact_summary) if artifact_summary else None,
            ),
        )
        self.conn.commit()
        result = self.get_by_id(harness_run_id)
        assert result is not None
        return result

    def get_by_id(self, harness_run_id: str) -> Optional[HarnessRun]:
        row = self.conn.execute(
            "SELECT harness_run_id, workspace_id, session_id, task_spec_id, "
            "harness_type, runtime_session_id, runtime_process_id, "
            "runtime_remote_process_id, status, control_capabilities_json, "
            "artifact_summary_json, started_at, ended_at, "
            "pgid, exit_code, exit_signal, tool_invocation_id "
            "FROM harness_runs WHERE harness_run_id = ?",
            (harness_run_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_session(self, session_id: str) -> List[HarnessRun]:
        rows = self.conn.execute(
            "SELECT harness_run_id, workspace_id, session_id, task_spec_id, "
            "harness_type, runtime_session_id, runtime_process_id, "
            "runtime_remote_process_id, status, control_capabilities_json, "
            "artifact_summary_json, started_at, ended_at, "
            "pgid, exit_code, exit_signal, tool_invocation_id "
            "FROM harness_runs WHERE session_id = ? ORDER BY started_at ASC",
            (session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_by_workspace(self, workspace_id: str) -> List[HarnessRun]:
        rows = self.conn.execute(
            "SELECT harness_run_id, workspace_id, session_id, task_spec_id, "
            "harness_type, runtime_session_id, runtime_process_id, "
            "runtime_remote_process_id, status, control_capabilities_json, "
            "artifact_summary_json, started_at, ended_at, "
            "pgid, exit_code, exit_signal, tool_invocation_id "
            "FROM harness_runs WHERE workspace_id = ? ORDER BY started_at ASC",
            (workspace_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update_status(
        self,
        harness_run_id: str,
        *,
        status: str,
        started_at: Optional[float] = None,
        ended_at: Optional[float] = None,
    ) -> Optional[HarnessRun]:
        """Update the status of a harness run. Optionally set started_at/ended_at."""
        updates: list[str] = ["status = ?"]
        params: list = [status]

        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at)
        if ended_at is not None:
            updates.append("ended_at = ?")
            params.append(ended_at)

        params.append(harness_run_id)
        self.conn.execute(
            f"UPDATE harness_runs SET {', '.join(updates)} WHERE harness_run_id = ?",
            params,
        )
        self.conn.commit()
        return self.get_by_id(harness_run_id)

    def update_runtime_ids(
        self,
        harness_run_id: str,
        *,
        runtime_session_id: Optional[str] = None,
        runtime_process_id: Optional[str] = None,
        runtime_remote_process_id: Optional[str] = None,
        pgid: Optional[str] = None,
    ) -> Optional[HarnessRun]:
        """Update runtime identifiers on a harness run."""
        updates: list[str] = []
        params: list = []

        if runtime_session_id is not None:
            updates.append("runtime_session_id = ?")
            params.append(runtime_session_id)
        if runtime_process_id is not None:
            updates.append("runtime_process_id = ?")
            params.append(runtime_process_id)
        if runtime_remote_process_id is not None:
            updates.append("runtime_remote_process_id = ?")
            params.append(runtime_remote_process_id)
        if pgid is not None:
            updates.append("pgid = ?")
            params.append(pgid)

        if not updates:
            return self.get_by_id(harness_run_id)

        params.append(harness_run_id)
        self.conn.execute(
            f"UPDATE harness_runs SET {', '.join(updates)} WHERE harness_run_id = ?",
            params,
        )
        self.conn.commit()
        return self.get_by_id(harness_run_id)

    def delete(self, harness_run_id: str) -> bool:
        """Delete a harness run. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM harness_runs WHERE harness_run_id = ?",
            (harness_run_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> HarnessRun:
        return HarnessRun(
            harness_run_id=row["harness_run_id"],
            workspace_id=row["workspace_id"],
            session_id=row["session_id"],
            task_spec_id=row["task_spec_id"],
            harness_type=row["harness_type"],
            runtime_session_id=row["runtime_session_id"],
            runtime_process_id=row["runtime_process_id"],
            runtime_remote_process_id=row["runtime_remote_process_id"],
            status=row["status"],
            control_capabilities_json=row["control_capabilities_json"],
            artifact_summary_json=row["artifact_summary_json"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            pgid=row["pgid"] if "pgid" in row.keys() else None,
            exit_code=row["exit_code"] if "exit_code" in row.keys() else None,
            exit_signal=row["exit_signal"] if "exit_signal" in row.keys() else None,
            tool_invocation_id=row["tool_invocation_id"] if "tool_invocation_id" in row.keys() else None,
        )
