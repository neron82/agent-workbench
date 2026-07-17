"""Tests for PermissionModel."""

import sqlite3
import uuid

import pytest

from agent_workbench.adapters.permission import PermissionModel
from agent_workbench.models.permission_request import (
    PermissionRequest,
)


def _seed_harness_run(db: sqlite3.Connection) -> str:
    """Seed a workspace + harness_run and return the harness_run_id."""
    wid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO workspaces (workspace_id, tenant_id, name, is_default, created_at) "
        "VALUES (?, ?, ?, 0, 0)",
        (wid, "tenant-1", "test-workspace"),
    )
    hid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO harness_runs "
        "(harness_run_id, workspace_id, session_id, harness_type, status) "
        "VALUES (?, ?, '', 'hermes', 'queued')",
        (hid, wid),
    )
    db.commit()
    return hid


@pytest.fixture
def pm(db: sqlite3.Connection) -> PermissionModel:
    """PermissionModel with no auto-approve or sensitive scopes."""
    return PermissionModel(db)


@pytest.fixture
def pm_with_policies(db: sqlite3.Connection) -> PermissionModel:
    """PermissionModel with auto-approve and sensitive scopes."""
    return PermissionModel(
        db,
        auto_approve_scopes=["file", "command"],
        sensitive_scopes=["remote_action", "task"],
    )


# ------------------------------------------------------------------
# request_permission
# ------------------------------------------------------------------

class TestPermissionRequest:
    def test_request_creates_pending(self, pm: PermissionModel, db: sqlite3.Connection):
        hid = _seed_harness_run(db)
        req = pm.request_permission(
            harness_run_id=hid,
            scope="command",
            action="rm -rf /tmp/test",
            reason="Cleanup",
            requested_by="agent-alpha",
        )
        assert isinstance(req, PermissionRequest)
        assert req.decision == "pending"
        assert req.scope == "command"
        assert req.requested_action == "rm -rf /tmp/test"
        assert req.requested_by == "agent-alpha"
        assert req.escalated_from_auto_approve is False

    def test_request_auto_approved(self, pm_with_policies: PermissionModel, db: sqlite3.Connection):
        hid = _seed_harness_run(db)
        req = pm_with_policies.request_permission(
            harness_run_id=hid,
            scope="file",
            action="cat /etc/config.yaml",
            reason="Read config",
            requested_by="agent-alpha",
        )
        assert req.decision == "approved"

    def test_request_sensitive_scope_not_auto_approved(self, pm_with_policies: PermissionModel, db: sqlite3.Connection):
        """Sensitive scopes should never be auto-approved (decision 26)."""
        hid = _seed_harness_run(db)
        req = pm_with_policies.request_permission(
            harness_run_id=hid,
            scope="remote_action",
            action="curl http://example.com",
            reason="Fetch data",
            requested_by="agent-alpha",
        )
        assert req.decision == "pending"

    def test_request_escalated_flag(self, pm: PermissionModel, db: sqlite3.Connection):
        hid = _seed_harness_run(db)
        req = pm.request_permission(
            harness_run_id=hid,
            scope="command",
            action="curl http://example.com",
            reason="Fetch data",
            requested_by="agent-alpha",
            escalated=True,
        )
        assert req.escalated_from_auto_approve is True


# ------------------------------------------------------------------
# check_permission
# ------------------------------------------------------------------

class TestCheckPermission:
    def test_check_pending(self, pm: PermissionModel, db: sqlite3.Connection):
        hid = _seed_harness_run(db)
        req = pm.request_permission(
            harness_run_id=hid,
            scope="command",
            action="test",
            requested_by="agent",
        )
        assert pm.check_permission(req.permission_request_id) == "pending"

    def test_check_approved(self, pm: PermissionModel, db: sqlite3.Connection):
        hid = _seed_harness_run(db)
        req = pm.request_permission(
            harness_run_id=hid,
            scope="command",
            action="test",
            requested_by="agent",
        )
        pm.approve(req.permission_request_id)
        assert pm.check_permission(req.permission_request_id) == "approved"

    def test_check_nonexistent_returns_expired(self, pm: PermissionModel):
        assert pm.check_permission("nonexistent-id") == "expired"


# ------------------------------------------------------------------
# approve / deny
# ------------------------------------------------------------------

class TestApproveDeny:
    def test_approve(self, pm: PermissionModel, db: sqlite3.Connection):
        hid = _seed_harness_run(db)
        req = pm.request_permission(
            harness_run_id=hid,
            scope="command",
            action="test",
            requested_by="agent",
        )
        updated = pm.approve(req.permission_request_id)
        assert updated.decision == "approved"
        assert updated.decided_at is not None

    def test_deny(self, pm: PermissionModel, db: sqlite3.Connection):
        hid = _seed_harness_run(db)
        req = pm.request_permission(
            harness_run_id=hid,
            scope="command",
            action="test",
            requested_by="agent",
        )
        updated = pm.deny(req.permission_request_id)
        assert updated.decision == "denied"
        assert updated.decided_at is not None

    def test_approve_nonexistent_raises(self, pm: PermissionModel):
        with pytest.raises(ValueError):
            pm.approve("nonexistent-id")

    def test_deny_nonexistent_raises(self, pm: PermissionModel):
        with pytest.raises(ValueError):
            pm.deny("nonexistent-id")


# ------------------------------------------------------------------
# is_auto_approved
# ------------------------------------------------------------------

class TestIsAutoApproved:
    def test_auto_approved_scope(self, pm_with_policies: PermissionModel):
        assert pm_with_policies.is_auto_approved("any-harness", "file") is True
        assert pm_with_policies.is_auto_approved("any-harness", "command") is True

    def test_not_auto_approved_scope(self, pm_with_policies: PermissionModel):
        assert pm_with_policies.is_auto_approved("any-harness", "remote_action") is False
        assert pm_with_policies.is_auto_approved("any-harness", "unknown_scope") is False

    def test_sensitive_overrides_auto_approve(self, pm_with_policies: PermissionModel):
        """Even if a scope were in both lists, sensitive wins (decision 26)."""
        assert pm_with_policies.is_auto_approved("any-harness", "remote_action") is False

    def test_empty_auto_approve(self, pm: PermissionModel):
        assert pm.is_auto_approved("any-harness", "file.read") is False


# ------------------------------------------------------------------
# should_escalate
# ------------------------------------------------------------------

class TestShouldEscalate:
    def test_sensitive_scope_escalates(self, pm_with_policies: PermissionModel):
        """Sensitive scopes always escalate (decision 26)."""
        assert pm_with_policies.should_escalate("any-harness", "remote_action") is True
        assert pm_with_policies.should_escalate("any-harness", "task") is True

    def test_unknown_scope_escalates(self, pm_with_policies: PermissionModel):
        assert pm_with_policies.should_escalate("any-harness", "unknown_scope") is True

    def test_auto_approved_scope_does_not_escalate(self, pm_with_policies: PermissionModel):
        assert pm_with_policies.should_escalate("any-harness", "file") is False

    def test_all_escalate_with_no_policies(self, pm: PermissionModel):
        """With no auto-approve or sensitive scopes, everything escalates."""
        assert pm.should_escalate("any-harness", "file") is True
        assert pm.should_escalate("any-harness", "command") is True
