"""PermissionRequest domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PermissionRequest:
    permission_request_id: str
    harness_run_id: str
    scope: str
    reason: str
    requested_action: str
    requested_by: str
    decision: str
    escalated_from_auto_approve: bool
    created_at: float
    decided_at: Optional[float]


class PermissionRequestRepository:
    """SQLite-backed repository for PermissionRequest entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        harness_run_id: str,
        scope: str,
        reason: str = "",
        requested_action: str,
        requested_by: str,
        decision: str = "pending",
        escalated_from_auto_approve: bool = False,
    ) -> PermissionRequest:
        """Insert a new permission request and return the persisted instance."""
        permission_request_id = uuid.uuid4().hex
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO permission_requests "
            "(permission_request_id, harness_run_id, scope, reason, "
            "requested_action, requested_by, decision, "
            "escalated_from_auto_approve, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                permission_request_id,
                harness_run_id,
                scope,
                reason,
                requested_action,
                requested_by,
                decision,
                int(escalated_from_auto_approve),
                created_at,
            ),
        )
        self.conn.commit()
        return PermissionRequest(
            permission_request_id=permission_request_id,
            harness_run_id=harness_run_id,
            scope=scope,
            reason=reason,
            requested_action=requested_action,
            requested_by=requested_by,
            decision=decision,
            escalated_from_auto_approve=escalated_from_auto_approve,
            created_at=created_at,
            decided_at=None,
        )

    def get_by_id(self, permission_request_id: str) -> Optional[PermissionRequest]:
        row = self.conn.execute(
            "SELECT permission_request_id, harness_run_id, scope, reason, "
            "requested_action, requested_by, decision, "
            "escalated_from_auto_approve, created_at, decided_at "
            "FROM permission_requests WHERE permission_request_id = ?",
            (permission_request_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_harness_run(self, harness_run_id: str) -> List[PermissionRequest]:
        rows = self.conn.execute(
            "SELECT permission_request_id, harness_run_id, scope, reason, "
            "requested_action, requested_by, decision, "
            "escalated_from_auto_approve, created_at, decided_at "
            "FROM permission_requests WHERE harness_run_id = ? ORDER BY created_at ASC",
            (harness_run_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update_decision(
        self, permission_request_id: str, *, decision: str, decided_at: Optional[float] = None
    ) -> Optional[PermissionRequest]:
        """Update the decision on a permission request. Returns the updated instance or None."""
        if decided_at is None:
            decided_at = time.time()
        cursor = self.conn.execute(
            "UPDATE permission_requests SET decision = ?, decided_at = ? "
            "WHERE permission_request_id = ?",
            (decision, decided_at, permission_request_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(permission_request_id)

    def delete(self, permission_request_id: str) -> bool:
        """Delete a permission request. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM permission_requests WHERE permission_request_id = ?",
            (permission_request_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> PermissionRequest:
        return PermissionRequest(
            permission_request_id=row["permission_request_id"],
            harness_run_id=row["harness_run_id"],
            scope=row["scope"],
            reason=row["reason"],
            requested_action=row["requested_action"],
            requested_by=row["requested_by"],
            decision=row["decision"],
            escalated_from_auto_approve=bool(row["escalated_from_auto_approve"]),
            created_at=row["created_at"],
            decided_at=row["decided_at"],
        )
