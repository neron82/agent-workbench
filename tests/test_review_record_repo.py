"""Tests for ReviewRecordRepository."""

import sqlite3
import uuid

from agent_workbench.models.review_record import ReviewRecord, ReviewRecordRepository


def _seed_workspace(db: sqlite3.Connection) -> str:
    wid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO workspaces (workspace_id, tenant_id, name, is_default, created_at) "
        "VALUES (?, ?, ?, 0, 0)",
        (wid, "tenant-1", "test-workspace"),
    )
    db.commit()
    return wid


def test_create_review_record(db: sqlite3.Connection) -> None:
    repo = ReviewRecordRepository(db)
    ws_id = _seed_workspace(db)
    rec = repo.create(
        workspace_id=ws_id,
        target_kind="task_spec",
        target_id="ts-1",
        verdict="pass",
        findings_ref="s3://findings/r1.json",
        criteria_eval={"accuracy": 0.95},
    )
    assert isinstance(rec, ReviewRecord)
    assert rec.review_id
    assert rec.workspace_id == ws_id
    assert rec.target_kind == "task_spec"
    assert rec.target_id == "ts-1"
    assert rec.verdict == "pass"
    assert rec.criteria_eval == {"accuracy": 0.95}
    assert rec.created_at > 0


def test_get_by_id(db: sqlite3.Connection) -> None:
    repo = ReviewRecordRepository(db)
    ws_id = _seed_workspace(db)
    rec = repo.create(
        workspace_id=ws_id,
        target_kind="artifact",
        target_id="art-1",
        verdict="conditional",
    )
    fetched = repo.get_by_id(rec.review_id)
    assert fetched is not None
    assert fetched.review_id == rec.review_id
    assert fetched.verdict == "conditional"
    assert repo.get_by_id("nonexistent") is None


def test_list_by_target(db: sqlite3.Connection) -> None:
    repo = ReviewRecordRepository(db)
    ws_id = _seed_workspace(db)
    repo.create(
        workspace_id=ws_id,
        target_kind="task_spec",
        target_id="ts-1",
        verdict="pass",
    )
    repo.create(
        workspace_id=ws_id,
        target_kind="task_spec",
        target_id="ts-1",
        verdict="fail",
    )
    repo.create(
        workspace_id=ws_id,
        target_kind="task_spec",
        target_id="ts-2",
        verdict="pass",
    )
    results = repo.list_by_target("task_spec", "ts-1")
    assert len(results) == 2
    assert all(r.target_kind == "task_spec" and r.target_id == "ts-1" for r in results)


def test_delete_review_record(db: sqlite3.Connection) -> None:
    repo = ReviewRecordRepository(db)
    ws_id = _seed_workspace(db)
    rec = repo.create(
        workspace_id=ws_id,
        target_kind="session",
        target_id="sess-1",
        verdict="blocked",
    )
    assert repo.delete(rec.review_id) is True
    assert repo.get_by_id(rec.review_id) is None
    assert repo.delete("nonexistent") is False
