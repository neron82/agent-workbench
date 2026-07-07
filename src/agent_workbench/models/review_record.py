"""ReviewRecord domain model and repository.

ReviewRecords are append-only — once created they should not be modified.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ReviewRecord:
    review_id: str
    workspace_id: str
    target_kind: str
    target_id: str
    reviewer_binding_id: Optional[str]
    verdict: str
    findings_ref: Optional[str]
    criteria_eval: Optional[Dict[str, Any]]
    created_at: float


class ReviewRecordRepository:
    """SQLite-backed repository for ReviewRecord entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        workspace_id: str,
        target_kind: str,
        target_id: str,
        reviewer_binding_id: Optional[str] = None,
        verdict: str,
        findings_ref: Optional[str] = None,
        criteria_eval: Optional[Dict[str, Any]] = None,
    ) -> ReviewRecord:
        """Insert a new review record and return the persisted instance."""
        review_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO review_records "
            "(review_id, workspace_id, target_kind, target_id, "
            "reviewer_binding_id, verdict, findings_ref, criteria_eval_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                review_id,
                workspace_id,
                target_kind,
                target_id,
                reviewer_binding_id,
                verdict,
                findings_ref,
                json.dumps(criteria_eval) if criteria_eval is not None else None,
                created_at,
            ),
        )
        self.conn.commit()
        return ReviewRecord(
            review_id=review_id,
            workspace_id=workspace_id,
            target_kind=target_kind,
            target_id=target_id,
            reviewer_binding_id=reviewer_binding_id,
            verdict=verdict,
            findings_ref=findings_ref,
            criteria_eval=criteria_eval,
            created_at=created_at,
        )

    def get_by_id(self, review_id: str) -> Optional[ReviewRecord]:
        row = self.conn.execute(
            "SELECT review_id, workspace_id, target_kind, target_id, "
            "reviewer_binding_id, verdict, findings_ref, criteria_eval_json, created_at "
            "FROM review_records WHERE review_id = ?",
            (review_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_target(
        self, target_kind: str, target_id: str
    ) -> List[ReviewRecord]:
        rows = self.conn.execute(
            "SELECT review_id, workspace_id, target_kind, target_id, "
            "reviewer_binding_id, verdict, findings_ref, criteria_eval_json, created_at "
            "FROM review_records WHERE target_kind = ? AND target_id = ? "
            "ORDER BY created_at ASC",
            (target_kind, target_id),
        ).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, review_id: str) -> bool:
        """Delete a review record. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM review_records WHERE review_id = ?",
            (review_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> ReviewRecord:
        return ReviewRecord(
            review_id=row["review_id"],
            workspace_id=row["workspace_id"],
            target_kind=row["target_kind"],
            target_id=row["target_id"],
            reviewer_binding_id=row["reviewer_binding_id"],
            verdict=row["verdict"],
            findings_ref=row["findings_ref"],
            criteria_eval=json.loads(row["criteria_eval_json"]) if row["criteria_eval_json"] else None,
            created_at=row["created_at"],
        )
