"""Permission model — product-layer permission gating for harness actions.

Decision 5: product-layer permission model for destructive/sensitive actions.
Decision 26: sensitive Work sessions may escalate to per-tool confirmation
             even if auto-approve is enabled.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional, Set

from agent_workbench.models.permission_request import (
    PermissionRequest,
    PermissionRequestRepository,
)


class PermissionModel:
    """Manages permission requests, auto-approval, and escalation.

    Uses PermissionRequestRepository for persistence. Supports configurable
    auto-approve scopes and sensitive scopes that always require explicit
    human approval (decision 26).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        auto_approve_scopes: Optional[List[str]] = None,
        sensitive_scopes: Optional[List[str]] = None,
    ) -> None:
        self._repo = PermissionRequestRepository(conn)
        self._auto_approve_scopes: Set[str] = set(auto_approve_scopes or [])
        self._sensitive_scopes: Set[str] = set(sensitive_scopes or [])

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def request_permission(
        self,
        harness_run_id: str,
        scope: str,
        action: str,
        reason: str = "",
        requested_by: str = "agent",
        escalated: bool = False,
    ) -> PermissionRequest:
        """Create a permission request.

        If the scope is in the auto-approve list and not sensitive,
        the request is auto-approved immediately.
        """
        auto_approved = self.is_auto_approved(harness_run_id, scope)

        if auto_approved:
            decision = "approved"
        else:
            decision = "pending"

        req = self._repo.create(
            harness_run_id=harness_run_id,
            scope=scope,
            reason=reason,
            requested_action=action,
            requested_by=requested_by,
            decision=decision,
            escalated_from_auto_approve=escalated,
        )
        return req

    def check_permission(self, permission_request_id: str) -> str:
        """Return the current decision for a permission request.

        Returns one of: pending, approved, denied, expired.
        """
        req = self._repo.get_by_id(permission_request_id)
        if req is None:
            return "expired"
        return req.decision

    def approve(self, permission_request_id: str) -> PermissionRequest:
        """Approve a pending permission request."""
        updated = self._repo.update_decision(
            permission_request_id, decision="approved"
        )
        if updated is None:
            raise ValueError(f"Permission request {permission_request_id} not found")
        return updated

    def deny(self, permission_request_id: str) -> PermissionRequest:
        """Deny a pending permission request."""
        updated = self._repo.update_decision(
            permission_request_id, decision="denied"
        )
        if updated is None:
            raise ValueError(f"Permission request {permission_request_id} not found")
        return updated

    # ------------------------------------------------------------------
    # Policy checks
    # ------------------------------------------------------------------

    def is_auto_approved(self, harness_run_id: str, scope: str) -> bool:
        """Check if a scope is in the auto-approve list.

        Sensitive scopes are never auto-approved (decision 26).
        """
        if scope in self._sensitive_scopes:
            return False
        return scope in self._auto_approve_scopes

    def should_escalate(self, harness_run_id: str, scope: str) -> bool:
        """Determine if a permission request should escalate to human review.

        Returns True if:
        - The scope is in the sensitive scopes list (decision 26).
        - The scope is NOT in the auto-approve list.

        Sensitive sessions always escalate even if auto-approve is enabled
        for that scope.
        """
        if scope in self._sensitive_scopes:
            return True
        if scope not in self._auto_approve_scopes:
            return True
        return False
