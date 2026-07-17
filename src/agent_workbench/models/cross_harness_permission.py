"""Cross-harness permission table.

When a user approves a cross-harness call (e.g. a Hermes agent wants
to call ``shell.run_command``), we record a row here so future calls of
the same kind skip the confirmation prompt.

Decisions:
- ``once`` — a single call is allowed; the row is deleted after the
  next dispatch consumes it
- ``permanent`` — all future calls of this kind are auto-approved
  until the session ends
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


CROSS_HARNESS_DECISIONS = ("once", "permanent")


@dataclass
class CrossHarnessPermission:
    permission_id: str
    session_id: str
    workspace_id: str
    agent_harness_type: Optional[str]  # None = "any agent harness"
    tool_harness_type: str
    decision: str
    created_at: float
    consumed_at: Optional[float] = None
    expires_at: Optional[float] = None


class CrossHarnessPermissionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def grant(
        self,
        *,
        session_id: str,
        workspace_id: str,
        agent_harness_type: Optional[str],
        tool_harness_type: str,
        decision: str,
    ) -> CrossHarnessPermission:
        if decision not in CROSS_HARNESS_DECISIONS:
            raise ValueError(
                f"Invalid decision: {decision!r}. Must be one of {CROSS_HARNESS_DECISIONS}"
            )
        permission_id = uuid.uuid4().hex
        now = time.time()
        # SQLite UNIQUE constraint: NULL is distinct from any value, so
        # we have to check existing rows manually when agent_harness_type
        # is None to avoid duplicate (session, NULL, harness, decision)
        # triples.
        existing = self._find_existing(
            session_id, agent_harness_type, tool_harness_type, decision
        )
        if existing is not None:
            return existing
        self.conn.execute(
            "INSERT INTO cross_harness_permissions "
            "(permission_id, session_id, workspace_id, agent_harness_type, "
            "tool_harness_type, decision, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                permission_id,
                session_id,
                workspace_id,
                agent_harness_type,
                tool_harness_type,
                decision,
                now,
            ),
        )
        self.conn.commit()
        return self.get_by_id(permission_id)  # type: ignore[return-value]

    def _find_existing(
        self,
        session_id: str,
        agent_harness_type: Optional[str],
        tool_harness_type: str,
        decision: str,
    ) -> Optional[CrossHarnessPermission]:
        if agent_harness_type is None:
            row = self.conn.execute(
                "SELECT * FROM cross_harness_permissions "
                "WHERE session_id = ? AND agent_harness_type IS NULL "
                "AND tool_harness_type = ? AND decision = ?",
                (session_id, tool_harness_type, decision),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM cross_harness_permissions "
                "WHERE session_id = ? AND agent_harness_type = ? "
                "AND tool_harness_type = ? AND decision = ?",
                (session_id, agent_harness_type, tool_harness_type, decision),
            ).fetchone()
        return self._row(row) if row else None

    def get_by_id(self, permission_id: str) -> Optional[CrossHarnessPermission]:
        row = self.conn.execute(
            "SELECT * FROM cross_harness_permissions WHERE permission_id = ?",
            (permission_id,),
        ).fetchone()
        return self._row(row) if row else None

    def is_allowed(
        self,
        *,
        session_id: str,
        agent_harness_type: Optional[str],
        tool_harness_type: str,
        require_permanent: bool = False,
    ) -> bool:
        """Return True if a permission row exists for this pair.

        Either a specific ``(session, agent_harness, tool_harness)``
        row OR a global ``(session, NULL, tool_harness)`` row counts.

        When ``require_permanent=True``, only 'permanent' rows match;
        'once' rows are ignored (they are consumed during dispatch).
        """
        decision_filter = "AND decision = 'permanent'" if require_permanent else ""
        # Try the specific match first.
        if agent_harness_type is not None:
            row = self.conn.execute(
                f"SELECT 1 FROM cross_harness_permissions "
                "WHERE session_id = ? AND agent_harness_type = ? "
                f"AND tool_harness_type = ? {decision_filter}",
                (session_id, agent_harness_type, tool_harness_type),
            ).fetchone()
            if row is not None:
                return True
        # Then the global match (any agent).
        row = self.conn.execute(
            f"SELECT 1 FROM cross_harness_permissions "
            "WHERE session_id = ? AND agent_harness_type IS NULL "
            f"AND tool_harness_type = ? {decision_filter}",
            (session_id, tool_harness_type),
        ).fetchone()
        return row is not None

    def consume_once(
        self,
        *,
        session_id: str,
        agent_harness_type: Optional[str],
        tool_harness_type: str,
    ) -> int:
        """Atomically delete exactly one matching ``once`` grant.

        The candidate lookup and deletion are one SQLite write statement, so
        concurrent connections cannot both report consuming the same row.
        A harness-specific grant takes precedence over a global grant.
        Returns 1 if a row was deleted, otherwise 0.

        'permanent' rows are kept; only 'once' rows go away so the user
        has to re-confirm for each individual call.
        """
        if agent_harness_type is None:
            row = self.conn.execute(
                "DELETE FROM cross_harness_permissions "
                "WHERE permission_id = ("
                "SELECT permission_id FROM cross_harness_permissions "
                "WHERE session_id = ? AND agent_harness_type IS NULL "
                "AND tool_harness_type = ? AND decision = 'once' "
                "ORDER BY created_at ASC LIMIT 1"
                ") RETURNING permission_id",
                (session_id, tool_harness_type),
            ).fetchone()
        else:
            row = self.conn.execute(
                "DELETE FROM cross_harness_permissions "
                "WHERE permission_id = ("
                "SELECT permission_id FROM cross_harness_permissions "
                "WHERE session_id = ? AND tool_harness_type = ? "
                "AND decision = 'once' "
                "AND (agent_harness_type = ? OR agent_harness_type IS NULL) "
                "ORDER BY CASE WHEN agent_harness_type = ? THEN 0 ELSE 1 END, "
                "created_at ASC LIMIT 1"
                ") RETURNING permission_id",
                (
                    session_id,
                    tool_harness_type,
                    agent_harness_type,
                    agent_harness_type,
                ),
            ).fetchone()
        self.conn.commit()
        return 1 if row is not None else 0

    def list_for_session(
        self, session_id: str
    ) -> List[CrossHarnessPermission]:
        rows = self.conn.execute(
            "SELECT * FROM cross_harness_permissions "
            "WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, permission_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM cross_harness_permissions WHERE permission_id = ?",
            (permission_id,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> CrossHarnessPermission:
        return CrossHarnessPermission(
            permission_id=row["permission_id"],
            session_id=row["session_id"],
            workspace_id=row["workspace_id"],
            agent_harness_type=row["agent_harness_type"],
            tool_harness_type=row["tool_harness_type"],
            decision=row["decision"],
            created_at=row["created_at"],
            consumed_at=row["consumed_at"],
            expires_at=row["expires_at"],
        )
