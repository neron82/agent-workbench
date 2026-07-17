"""Participant transfer domain model and repository."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


TRANSFER_STATUSES = ("pending", "completed", "failed", "cancelled")
TRANSFER_INITIATED_BY = ("user", "orchestrator", "system")


@dataclass
class ParticipantTransfer:
    transfer_id: str
    source_session_id: str
    target_session_id: str
    initiated_by: str
    transferred_participants: List[Dict[str, Any]]
    context_summary: str
    status: str
    created_at: float
    completed_at: Optional[float]


class ParticipantTransferRepository:
    """SQLite-backed repository for participant transfer records."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        source_session_id: str,
        target_session_id: str,
        initiated_by: str = "user",
        transferred_participants: Optional[List[Dict[str, Any]]] = None,
        context_summary: str = "",
    ) -> ParticipantTransfer:
        if initiated_by not in TRANSFER_INITIATED_BY:
            raise ValueError(
                f"Invalid initiated_by: {initiated_by!r}. Must be one of {TRANSFER_INITIATED_BY}"
            )
        transfer_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO participant_transfers "
            "(transfer_id, source_session_id, target_session_id, initiated_by, "
            "transferred_participants_json, context_summary, status, created_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, NULL)",
            (
                transfer_id,
                source_session_id,
                target_session_id,
                initiated_by,
                json.dumps(transferred_participants or []),
                context_summary,
                created_at,
            ),
        )
        self.conn.commit()
        return self.get_by_id(transfer_id)  # type: ignore[return-value]

    def get_by_id(self, transfer_id: str) -> Optional[ParticipantTransfer]:
        row = self.conn.execute(
            "SELECT transfer_id, source_session_id, target_session_id, initiated_by, "
            "transferred_participants_json, context_summary, status, created_at, completed_at "
            "FROM participant_transfers WHERE transfer_id = ?",
            (transfer_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_source(self, source_session_id: str) -> List[ParticipantTransfer]:
        rows = self.conn.execute(
            "SELECT transfer_id, source_session_id, target_session_id, initiated_by, "
            "transferred_participants_json, context_summary, status, created_at, completed_at "
            "FROM participant_transfers WHERE source_session_id = ? ORDER BY created_at DESC",
            (source_session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_by_target(self, target_session_id: str) -> List[ParticipantTransfer]:
        rows = self.conn.execute(
            "SELECT transfer_id, source_session_id, target_session_id, initiated_by, "
            "transferred_participants_json, context_summary, status, created_at, completed_at "
            "FROM participant_transfers WHERE target_session_id = ? ORDER BY created_at DESC",
            (target_session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update_status(
        self, transfer_id: str, *, status: str
    ) -> Optional[ParticipantTransfer]:
        if status not in TRANSFER_STATUSES:
            raise ValueError(
                f"Invalid status: {status!r}. Must be one of {TRANSFER_STATUSES}"
            )
        completed_at = time.time() if status in ("completed", "failed", "cancelled") else None
        self.conn.execute(
            "UPDATE participant_transfers SET status = ?, completed_at = ? WHERE transfer_id = ?",
            (status, completed_at, transfer_id),
        )
        self.conn.commit()
        return self.get_by_id(transfer_id)

    @staticmethod
    def _row(row: sqlite3.Row) -> ParticipantTransfer:
        return ParticipantTransfer(
            transfer_id=row["transfer_id"],
            source_session_id=row["source_session_id"],
            target_session_id=row["target_session_id"],
            initiated_by=row["initiated_by"],
            transferred_participants=json.loads(row["transferred_participants_json"] or "[]"),
            context_summary=row["context_summary"],
            status=row["status"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )