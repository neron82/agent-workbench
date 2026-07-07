"""ReplayRecord domain model and repository."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ReplayRecord:
    replay_id: str
    source_session_id: str
    source_harness_run_id: Optional[str]
    fork_id: str
    checkpoint: Optional[Dict[str, Any]]
    replay_scope: str
    equivalence_rule: str
    outcome: str
    created_at: float


class ReplayRecordRepository:
    """SQLite-backed repository for ReplayRecord entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        source_session_id: str,
        source_harness_run_id: Optional[str] = None,
        fork_id: str,
        checkpoint: Optional[Dict[str, Any]] = None,
        replay_scope: str = "",
        equivalence_rule: str = "final_state_plus_reviewer_judgment",
        outcome: str = "completed",
    ) -> ReplayRecord:
        """Insert a new replay record and return the persisted instance."""
        replay_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO replay_records "
            "(replay_id, source_session_id, source_harness_run_id, fork_id, "
            "checkpoint_json, replay_scope, equivalence_rule, outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                replay_id,
                source_session_id,
                source_harness_run_id,
                fork_id,
                json.dumps(checkpoint) if checkpoint is not None else None,
                replay_scope,
                equivalence_rule,
                outcome,
                created_at,
            ),
        )
        self.conn.commit()
        return ReplayRecord(
            replay_id=replay_id,
            source_session_id=source_session_id,
            source_harness_run_id=source_harness_run_id,
            fork_id=fork_id,
            checkpoint=checkpoint,
            replay_scope=replay_scope,
            equivalence_rule=equivalence_rule,
            outcome=outcome,
            created_at=created_at,
        )

    def get_by_id(self, replay_id: str) -> Optional[ReplayRecord]:
        row = self.conn.execute(
            "SELECT replay_id, source_session_id, source_harness_run_id, fork_id, "
            "checkpoint_json, replay_scope, equivalence_rule, outcome, created_at "
            "FROM replay_records WHERE replay_id = ?",
            (replay_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_session(self, source_session_id: str) -> List[ReplayRecord]:
        rows = self.conn.execute(
            "SELECT replay_id, source_session_id, source_harness_run_id, fork_id, "
            "checkpoint_json, replay_scope, equivalence_rule, outcome, created_at "
            "FROM replay_records WHERE source_session_id = ? ORDER BY created_at ASC",
            (source_session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, replay_id: str) -> bool:
        """Delete a replay record. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM replay_records WHERE replay_id = ?",
            (replay_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> ReplayRecord:
        return ReplayRecord(
            replay_id=row["replay_id"],
            source_session_id=row["source_session_id"],
            source_harness_run_id=row["source_harness_run_id"],
            fork_id=row["fork_id"],
            checkpoint=json.loads(row["checkpoint_json"]) if row["checkpoint_json"] else None,
            replay_scope=row["replay_scope"],
            equivalence_rule=row["equivalence_rule"],
            outcome=row["outcome"],
            created_at=row["created_at"],
        )
