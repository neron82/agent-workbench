"""Artifact domain model and repository.

Artifacts are immutable once created. Revisions are new rows with
``predecessor_artifact_id`` linking back to the original.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Artifact:
    artifact_id: str
    workspace_id: str
    producer_session_id: str
    producer_harness_run_id: Optional[str]
    task_spec_id: Optional[str]
    artifact_kind: str
    title: str
    content_ref: Optional[str]
    content_hash: Optional[str]
    predecessor_artifact_id: Optional[str]
    created_at: float


class ArtifactRepository:
    """SQLite-backed repository for Artifact entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        workspace_id: str,
        producer_session_id: str,
        producer_harness_run_id: Optional[str] = None,
        task_spec_id: Optional[str] = None,
        artifact_kind: str,
        title: str = "",
        content_ref: Optional[str] = None,
        content_hash: Optional[str] = None,
        predecessor_artifact_id: Optional[str] = None,
    ) -> Artifact:
        """Insert a new artifact and return the persisted instance."""
        artifact_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO artifacts "
            "(artifact_id, workspace_id, producer_session_id, "
            "producer_harness_run_id, task_spec_id, artifact_kind, title, "
            "content_ref, content_hash, predecessor_artifact_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact_id,
                workspace_id,
                producer_session_id,
                producer_harness_run_id,
                task_spec_id,
                artifact_kind,
                title,
                content_ref,
                content_hash,
                predecessor_artifact_id,
                created_at,
            ),
        )
        self.conn.commit()
        return Artifact(
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            producer_session_id=producer_session_id,
            producer_harness_run_id=producer_harness_run_id,
            task_spec_id=task_spec_id,
            artifact_kind=artifact_kind,
            title=title,
            content_ref=content_ref,
            content_hash=content_hash,
            predecessor_artifact_id=predecessor_artifact_id,
            created_at=created_at,
        )

    def get_by_id(self, artifact_id: str) -> Optional[Artifact]:
        row = self.conn.execute(
            "SELECT artifact_id, workspace_id, producer_session_id, "
            "producer_harness_run_id, task_spec_id, artifact_kind, title, "
            "content_ref, content_hash, predecessor_artifact_id, created_at "
            "FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_session(self, producer_session_id: str) -> List[Artifact]:
        rows = self.conn.execute(
            "SELECT artifact_id, workspace_id, producer_session_id, "
            "producer_harness_run_id, task_spec_id, artifact_kind, title, "
            "content_ref, content_hash, predecessor_artifact_id, created_at "
            "FROM artifacts WHERE producer_session_id = ? ORDER BY created_at ASC",
            (producer_session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_by_task_spec(self, task_spec_id: str) -> List[Artifact]:
        rows = self.conn.execute(
            "SELECT artifact_id, workspace_id, producer_session_id, "
            "producer_harness_run_id, task_spec_id, artifact_kind, title, "
            "content_ref, content_hash, predecessor_artifact_id, created_at "
            "FROM artifacts WHERE task_spec_id = ? ORDER BY created_at ASC",
            (task_spec_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, artifact_id: str) -> bool:
        """Delete an artifact. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> Artifact:
        return Artifact(
            artifact_id=row["artifact_id"],
            workspace_id=row["workspace_id"],
            producer_session_id=row["producer_session_id"],
            producer_harness_run_id=row["producer_harness_run_id"],
            task_spec_id=row["task_spec_id"],
            artifact_kind=row["artifact_kind"],
            title=row["title"],
            content_ref=row["content_ref"],
            content_hash=row["content_hash"],
            predecessor_artifact_id=row["predecessor_artifact_id"],
            created_at=row["created_at"],
        )
