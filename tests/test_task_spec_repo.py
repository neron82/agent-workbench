"""Tests for TaskSpecRepository."""

import sqlite3
import uuid

from agent_workbench.models.task_spec import TaskSpec, TaskSpecRepository


def _seed_workspace(db: sqlite3.Connection) -> str:
    wid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO workspaces (workspace_id, tenant_id, name, is_default, created_at) "
        "VALUES (?, ?, ?, 0, 0)",
        (wid, "tenant-1", "test-workspace"),
    )
    db.commit()
    return wid


def test_create_task_spec(db: sqlite3.Connection) -> None:
    repo = TaskSpecRepository(db)
    ws_id = _seed_workspace(db)
    ts = repo.create(
        workspace_id=ws_id,
        objective="Build a CLI tool",
        scope_in={"files": ["main.py"]},
        risk_level="low",
    )
    assert isinstance(ts, TaskSpec)
    assert ts.task_spec_id
    assert ts.workspace_id == ws_id
    assert ts.objective == "Build a CLI tool"
    assert ts.scope_in == {"files": ["main.py"]}
    assert ts.approval_status == "draft"
    assert ts.created_at > 0


def test_get_by_id(db: sqlite3.Connection) -> None:
    repo = TaskSpecRepository(db)
    ws_id = _seed_workspace(db)
    ts = repo.create(workspace_id=ws_id, objective="Test")
    fetched = repo.get_by_id(ts.task_spec_id)
    assert fetched is not None
    assert fetched.task_spec_id == ts.task_spec_id
    assert fetched.objective == "Test"
    assert repo.get_by_id("nonexistent") is None


def test_list_by_workspace(db: sqlite3.Connection) -> None:
    repo = TaskSpecRepository(db)
    ws_id = _seed_workspace(db)
    repo.create(workspace_id=ws_id, objective="First")
    repo.create(workspace_id=ws_id, objective="Second")
    ws_id2 = _seed_workspace(db)
    repo.create(workspace_id=ws_id2, objective="Other")
    results = repo.list_by_workspace(ws_id)
    assert len(results) == 2
    assert all(r.workspace_id == ws_id for r in results)


def test_update_approval_status(db: sqlite3.Connection) -> None:
    repo = TaskSpecRepository(db)
    ws_id = _seed_workspace(db)
    ts = repo.create(workspace_id=ws_id, objective="Test")
    updated = repo.update_approval_status(ts.task_spec_id, approval_status="approved")
    assert updated is not None
    assert updated.approval_status == "approved"
    assert repo.update_approval_status("nonexistent", approval_status="approved") is None


def test_update_fields(db: sqlite3.Connection) -> None:
    repo = TaskSpecRepository(db)
    ws_id = _seed_workspace(db)
    ts = repo.create(workspace_id=ws_id, objective="Original")
    updated = repo.update(ts.task_spec_id, objective="Updated", risk_level="high")
    assert updated is not None
    assert updated.objective == "Updated"
    assert updated.risk_level == "high"
    assert updated.updated_at >= updated.created_at


def test_delete_task_spec(db: sqlite3.Connection) -> None:
    repo = TaskSpecRepository(db)
    ws_id = _seed_workspace(db)
    ts = repo.create(workspace_id=ws_id, objective="Delete me")
    assert repo.delete(ts.task_spec_id) is True
    assert repo.get_by_id(ts.task_spec_id) is None
    assert repo.delete("nonexistent") is False
