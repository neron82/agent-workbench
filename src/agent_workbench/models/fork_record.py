"""Fork record domain model and repository."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ForkRecord:
    fork_id: str
    parent_session_id: str
    child_session_id: str
    fork_kind: str
    fork_reason: str
    initiated_by: str
    summary_ref: Optional[str]
    decisions_json: Optional[Dict[str, Any]]
    assumptions_json: Optional[Dict[str, Any]]
    open_questions_json: Optional[Dict[str, Any]]
    relevant_artifacts_json: Optional[Dict[str, Any]]
    bootstrap_context_role_internal: str
    checkpoint_json: Optional[Dict[str, Any]]
    created_at: float


class ForkRecordRepository:
    """SQLite-backed repository for ForkRecord entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        parent_session_id: str,
        child_session_id: str,
        fork_kind: str,
        fork_reason: str = "",
        initiated_by: str = "user",
        summary_ref: Optional[str] = None,
        decisions_json: Optional[Dict[str, Any]] = None,
        assumptions_json: Optional[Dict[str, Any]] = None,
        open_questions_json: Optional[Dict[str, Any]] = None,
        relevant_artifacts_json: Optional[Dict[str, Any]] = None,
        bootstrap_context_role_internal: str = "fork_context",
        checkpoint_json: Optional[Dict[str, Any]] = None,
    ) -> ForkRecord:
        """Insert a new fork record and return the persisted instance."""
        fork_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO fork_records ("
            "fork_id, parent_session_id, child_session_id, fork_kind, fork_reason, "
            "initiated_by, summary_ref, decisions_json, assumptions_json, "
            "open_questions_json, relevant_artifacts_json, "
            "bootstrap_context_role_internal, checkpoint_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fork_id,
                parent_session_id,
                child_session_id,
                fork_kind,
                fork_reason,
                initiated_by,
                summary_ref,
                json.dumps(decisions_json) if decisions_json is not None else None,
                json.dumps(assumptions_json) if assumptions_json is not None else None,
                json.dumps(open_questions_json) if open_questions_json is not None else None,
                json.dumps(relevant_artifacts_json) if relevant_artifacts_json is not None else None,
                bootstrap_context_role_internal,
                json.dumps(checkpoint_json) if checkpoint_json is not None else None,
                created_at,
            ),
        )
        self.conn.commit()
        return ForkRecord(
            fork_id=fork_id,
            parent_session_id=parent_session_id,
            child_session_id=child_session_id,
            fork_kind=fork_kind,
            fork_reason=fork_reason,
            initiated_by=initiated_by,
            summary_ref=summary_ref,
            decisions_json=decisions_json,
            assumptions_json=assumptions_json,
            open_questions_json=open_questions_json,
            relevant_artifacts_json=relevant_artifacts_json,
            bootstrap_context_role_internal=bootstrap_context_role_internal,
            checkpoint_json=checkpoint_json,
            created_at=created_at,
        )

    def get_by_id(self, fork_id: str) -> Optional[ForkRecord]:
        row = self.conn.execute(
            "SELECT fork_id, parent_session_id, child_session_id, fork_kind, "
            "fork_reason, initiated_by, summary_ref, decisions_json, assumptions_json, "
            "open_questions_json, relevant_artifacts_json, "
            "bootstrap_context_role_internal, checkpoint_json, created_at "
            "FROM fork_records WHERE fork_id = ?",
            (fork_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def get_by_child_session(self, child_session_id: str) -> Optional[ForkRecord]:
        row = self.conn.execute(
            "SELECT fork_id, parent_session_id, child_session_id, fork_kind, "
            "fork_reason, initiated_by, summary_ref, decisions_json, assumptions_json, "
            "open_questions_json, relevant_artifacts_json, "
            "bootstrap_context_role_internal, checkpoint_json, created_at "
            "FROM fork_records WHERE child_session_id = ? LIMIT 1",
            (child_session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def get_by_parent_session(self, parent_session_id: str) -> List[ForkRecord]:
        rows = self.conn.execute(
            "SELECT fork_id, parent_session_id, child_session_id, fork_kind, "
            "fork_reason, initiated_by, summary_ref, decisions_json, assumptions_json, "
            "open_questions_json, relevant_artifacts_json, "
            "bootstrap_context_role_internal, checkpoint_json, created_at "
            "FROM fork_records WHERE parent_session_id = ? ORDER BY created_at DESC",
            (parent_session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_by_kind(self, fork_kind: str) -> List[ForkRecord]:
        rows = self.conn.execute(
            "SELECT fork_id, parent_session_id, child_session_id, fork_kind, "
            "fork_reason, initiated_by, summary_ref, decisions_json, assumptions_json, "
            "open_questions_json, relevant_artifacts_json, "
            "bootstrap_context_role_internal, checkpoint_json, created_at "
            "FROM fork_records WHERE fork_kind = ? ORDER BY created_at DESC",
            (fork_kind,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, fork_id: str) -> bool:
        """Delete a fork record. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM fork_records WHERE fork_id = ?",
            (fork_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> ForkRecord:
        return ForkRecord(
            fork_id=row["fork_id"],
            parent_session_id=row["parent_session_id"],
            child_session_id=row["child_session_id"],
            fork_kind=row["fork_kind"],
            fork_reason=row["fork_reason"],
            initiated_by=row["initiated_by"],
            summary_ref=row["summary_ref"],
            decisions_json=_json_loads(row["decisions_json"]),
            assumptions_json=_json_loads(row["assumptions_json"]),
            open_questions_json=_json_loads(row["open_questions_json"]),
            relevant_artifacts_json=_json_loads(row["relevant_artifacts_json"]),
            bootstrap_context_role_internal=row["bootstrap_context_role_internal"],
            checkpoint_json=_json_loads(row["checkpoint_json"]),
            created_at=row["created_at"],
        )


def _json_loads(value: Optional[str]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    return json.loads(value)
