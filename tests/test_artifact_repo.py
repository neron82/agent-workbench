"""Tests for ArtifactRepository."""

import sqlite3
import uuid

from agent_workbench.models.artifact import Artifact, ArtifactRepository


def _seed_workspace(db: sqlite3.Connection) -> str:
    wid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO workspaces (workspace_id, tenant_id, name, is_default, created_at) "
        "VALUES (?, ?, ?, 0, 0)",
        (wid, "tenant-1", "test-workspace"),
    )
    db.commit()
    return wid


def test_create_artifact(db: sqlite3.Connection) -> None:
    repo = ArtifactRepository(db)
    ws_id = _seed_workspace(db)
    art = repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-1",
        artifact_kind="code",
        title="main.py",
        content_hash="sha256-abc",
    )
    assert isinstance(art, Artifact)
    assert art.artifact_id
    assert art.workspace_id == ws_id
    assert art.producer_session_id == "sess-1"
    assert art.artifact_kind == "code"
    assert art.content_hash == "sha256-abc"
    assert art.created_at > 0


def test_create_revision_artifact(db: sqlite3.Connection) -> None:
    repo = ArtifactRepository(db)
    ws_id = _seed_workspace(db)
    original = repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-1",
        artifact_kind="code",
        title="main.py v1",
    )
    revision = repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-1",
        artifact_kind="code",
        title="main.py v2",
        predecessor_artifact_id=original.artifact_id,
    )
    assert revision.predecessor_artifact_id == original.artifact_id
    assert revision.artifact_id != original.artifact_id


def test_get_by_id(db: sqlite3.Connection) -> None:
    repo = ArtifactRepository(db)
    ws_id = _seed_workspace(db)
    art = repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-1",
        artifact_kind="doc",
    )
    fetched = repo.get_by_id(art.artifact_id)
    assert fetched is not None
    assert fetched.artifact_id == art.artifact_id
    assert repo.get_by_id("nonexistent") is None


def test_list_by_session(db: sqlite3.Connection) -> None:
    repo = ArtifactRepository(db)
    ws_id = _seed_workspace(db)
    repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-1",
        artifact_kind="code",
    )
    repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-1",
        artifact_kind="doc",
    )
    repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-2",
        artifact_kind="code",
    )
    results = repo.list_by_session("sess-1")
    assert len(results) == 2
    assert all(r.producer_session_id == "sess-1" for r in results)


def test_list_by_task_spec(db: sqlite3.Connection) -> None:
    repo = ArtifactRepository(db)
    ws_id = _seed_workspace(db)
    # Seed a task_spec so we can reference it
    task_spec_id = uuid.uuid4().hex
    db.execute(
        "INSERT INTO task_specs "
        "(task_spec_id, workspace_id, objective, approval_status, created_at, updated_at) "
        "VALUES (?, ?, '', 'draft', 0, 0)",
        (task_spec_id, ws_id),
    )
    db.commit()

    repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-1",
        artifact_kind="code",
        task_spec_id=task_spec_id,
    )
    repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-1",
        artifact_kind="code",
        task_spec_id=task_spec_id,
    )
    repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-1",
        artifact_kind="code",
    )
    results = repo.list_by_task_spec(task_spec_id)
    assert len(results) == 2
    assert all(r.task_spec_id == task_spec_id for r in results)


def test_delete_artifact(db: sqlite3.Connection) -> None:
    repo = ArtifactRepository(db)
    ws_id = _seed_workspace(db)
    art = repo.create(
        workspace_id=ws_id,
        producer_session_id="sess-1",
        artifact_kind="code",
    )
    assert repo.delete(art.artifact_id) is True
    assert repo.get_by_id(art.artifact_id) is None
    assert repo.delete("nonexistent") is False
