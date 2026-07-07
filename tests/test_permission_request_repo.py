"""Tests for PermissionRequestRepository."""

import sqlite3
import uuid

from agent_workbench.models.permission_request import (
    PermissionRequest,
    PermissionRequestRepository,
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


def test_create_permission_request(db: sqlite3.Connection) -> None:
    repo = PermissionRequestRepository(db)
    hid = _seed_harness_run(db)
    req = repo.create(
        harness_run_id=hid,
        scope="file",
        reason="Need to read config",
        requested_action="cat /etc/config.yaml",
        requested_by="agent-alpha",
    )
    assert isinstance(req, PermissionRequest)
    assert req.permission_request_id
    assert req.harness_run_id == hid
    assert req.scope == "file"
    assert req.reason == "Need to read config"
    assert req.requested_action == "cat /etc/config.yaml"
    assert req.requested_by == "agent-alpha"
    assert req.decision == "pending"
    assert req.escalated_from_auto_approve is False
    assert req.decided_at is None
    assert req.created_at > 0


def test_get_by_id(db: sqlite3.Connection) -> None:
    repo = PermissionRequestRepository(db)
    hid = _seed_harness_run(db)
    req = repo.create(
        harness_run_id=hid,
        scope="command",
        requested_action="rm -rf /tmp/test",
        requested_by="agent-beta",
    )
    fetched = repo.get_by_id(req.permission_request_id)
    assert fetched is not None
    assert fetched.permission_request_id == req.permission_request_id
    assert fetched.scope == "command"
    assert repo.get_by_id("nonexistent") is None


def test_list_by_harness_run(db: sqlite3.Connection) -> None:
    repo = PermissionRequestRepository(db)
    hid1 = _seed_harness_run(db)
    hid2 = _seed_harness_run(db)
    repo.create(
        harness_run_id=hid1,
        scope="file",
        requested_action="read",
        requested_by="agent-a",
    )
    repo.create(
        harness_run_id=hid1,
        scope="command",
        requested_action="write",
        requested_by="agent-a",
    )
    repo.create(
        harness_run_id=hid2,
        scope="tool",
        requested_action="execute",
        requested_by="agent-b",
    )
    results = repo.list_by_harness_run(hid1)
    assert len(results) == 2
    assert all(r.harness_run_id == hid1 for r in results)


def test_update_decision(db: sqlite3.Connection) -> None:
    repo = PermissionRequestRepository(db)
    hid = _seed_harness_run(db)
    req = repo.create(
        harness_run_id=hid,
        scope="command",
        requested_action="curl http://example.com",
        requested_by="agent-c",
    )
    updated = repo.update_decision(
        req.permission_request_id, decision="approved", decided_at=1000.0
    )
    assert updated is not None
    assert updated.decision == "approved"
    assert updated.decided_at == 1000.0
    assert repo.update_decision("nonexistent", decision="denied") is None


def test_delete_permission_request(db: sqlite3.Connection) -> None:
    repo = PermissionRequestRepository(db)
    hid = _seed_harness_run(db)
    req = repo.create(
        harness_run_id=hid,
        scope="remote_action",
        requested_action="ssh host",
        requested_by="agent-d",
    )
    assert repo.delete(req.permission_request_id) is True
    assert repo.get_by_id(req.permission_request_id) is None
    assert repo.delete("nonexistent") is False
