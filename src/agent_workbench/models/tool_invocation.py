"""ToolInvocation domain model and repository.

A ToolInvocation is the durable record of one tool call that came back
from a provider's tool_calls response.  It links the agent's decision
(tool_id, arguments) to the HarnessRun that the dispatcher created.

This is the forensic chain: chat message -> tool_invocation -> harness_run
-> transcript lines.

When the dispatcher detects a cross-harness call (agent's configured
harness ≠ tool's harness), the invocation goes into
``pending_confirmation`` status.  The user then either denies it or
allows it (``once`` / ``permanent``) via the UI; only then does the
invocation transition to ``running`` → ``completed``/``failed``.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


TOOL_INVOCATION_STATUSES = (
    "pending", "running", "completed", "failed", "denied",
    "pending_confirmation",
)


@dataclass
class ToolInvocation:
    invocation_id: str
    session_id: str
    workspace_id: str
    tool_id: str
    tool_name: str
    tool_harness_type: str
    arguments_json: Dict[str, Any]
    status: str
    result_text: Optional[str]
    error_text: Optional[str]
    harness_run_id: Optional[str]
    confirmation_message_id: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_reason: Optional[str] = None
    created_at: float = 0.0
    completed_at: Optional[float] = None


class ToolInvocationRepository:
    """SQLite-backed repository for ToolInvocation entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        *,
        session_id: str,
        workspace_id: str,
        tool_id: str,
        tool_name: str,
        tool_harness_type: str = "",
        arguments: Optional[Dict[str, Any]] = None,
        status: str = "pending",
        requires_confirmation: bool = False,
        confirmation_reason: Optional[str] = None,
    ) -> ToolInvocation:
        if status not in TOOL_INVOCATION_STATUSES:
            raise ValueError(
                f"Invalid status: {status!r}. Must be one of {TOOL_INVOCATION_STATUSES}"
            )
        invocation_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO tool_invocations "
            "(invocation_id, session_id, workspace_id, tool_id, tool_name, "
            "tool_harness_type, arguments_json, status, result_text, "
            "error_text, harness_run_id, requires_confirmation, "
            "confirmation_reason, created_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, NULL)",
            (
                invocation_id,
                session_id,
                workspace_id,
                tool_id,
                tool_name,
                tool_harness_type,
                json.dumps(arguments or {}, sort_keys=True),
                status,
                1 if requires_confirmation else 0,
                confirmation_reason,
                now,
            ),
        )
        self.conn.commit()
        return self.get_by_id(invocation_id)  # type: ignore[return-value]

    def get_by_id(self, invocation_id: str) -> Optional[ToolInvocation]:
        row = self.conn.execute(
            "SELECT invocation_id, session_id, workspace_id, tool_id, tool_name, "
            "tool_harness_type, arguments_json, status, result_text, "
            "error_text, harness_run_id, confirmation_message_id, "
            "requires_confirmation, confirmation_reason, "
            "created_at, completed_at "
            "FROM tool_invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_session(self, session_id: str) -> List[ToolInvocation]:
        rows = self.conn.execute(
            "SELECT invocation_id, session_id, workspace_id, tool_id, tool_name, "
            "tool_harness_type, arguments_json, status, result_text, "
            "error_text, harness_run_id, confirmation_message_id, "
            "requires_confirmation, confirmation_reason, "
            "created_at, completed_at "
            "FROM tool_invocations WHERE session_id = ? "
            "ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_pending_confirmation(self, session_id: str) -> List[ToolInvocation]:
        rows = self.conn.execute(
            "SELECT invocation_id, session_id, workspace_id, tool_id, tool_name, "
            "tool_harness_type, arguments_json, status, result_text, "
            "error_text, harness_run_id, confirmation_message_id, "
            "requires_confirmation, confirmation_reason, "
            "created_at, completed_at "
            "FROM tool_invocations "
            "WHERE session_id = ? AND status = 'pending_confirmation' "
            "ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update_status(
        self,
        invocation_id: str,
        *,
        status: str,
        result_text: Optional[str] = None,
        error_text: Optional[str] = None,
        harness_run_id: Optional[str] = None,
    ) -> Optional[ToolInvocation]:
        if status not in TOOL_INVOCATION_STATUSES:
            raise ValueError(
                f"Invalid status: {status!r}. Must be one of {TOOL_INVOCATION_STATUSES}"
            )
        existing = self.get_by_id(invocation_id)
        if existing is None:
            return None

        updates: list[str] = ["status = ?"]
        params: list = [status]
        if result_text is not None:
            updates.append("result_text = ?")
            params.append(result_text)
        if error_text is not None:
            updates.append("error_text = ?")
            params.append(error_text)
        if harness_run_id is not None:
            updates.append("harness_run_id = ?")
            params.append(harness_run_id)

        is_terminal = status in ("completed", "failed", "denied")
        if is_terminal:
            updates.append("completed_at = ?")
            params.append(time.time())

        params.append(invocation_id)
        self.conn.execute(
            f"UPDATE tool_invocations SET {', '.join(updates)} "
            "WHERE invocation_id = ?",
            params,
        )
        self.conn.commit()
        return self.get_by_id(invocation_id)

    def set_confirmation_message_id(
        self, invocation_id: str, message_id: str
    ) -> Optional[ToolInvocation]:
        self.conn.execute(
            "UPDATE tool_invocations SET confirmation_message_id = ? "
            "WHERE invocation_id = ?",
            (message_id, invocation_id),
        )
        self.conn.commit()
        return self.get_by_id(invocation_id)

    @staticmethod
    def _row(row: sqlite3.Row) -> ToolInvocation:
        raw = row["arguments_json"]
        try:
            args = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            args = {}
        return ToolInvocation(
            invocation_id=row["invocation_id"],
            session_id=row["session_id"],
            workspace_id=row["workspace_id"],
            tool_id=row["tool_id"],
            tool_name=row["tool_name"],
            tool_harness_type=row["tool_harness_type"],
            arguments_json=args,
            status=row["status"],
            result_text=row["result_text"],
            error_text=row["error_text"],
            harness_run_id=row["harness_run_id"],
            confirmation_message_id=row["confirmation_message_id"],
            requires_confirmation=bool(row["requires_confirmation"]),
            confirmation_reason=row["confirmation_reason"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )
