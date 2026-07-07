"""Tests for ReplayRecordRepository."""

import sqlite3
import uuid

from agent_workbench.models.replay_record import ReplayRecord, ReplayRecordRepository


def _seed_fork(db: sqlite3.Connection) -> str:
    fork_id = uuid.uuid4().hex
    db.execute(
        "INSERT INTO fork_records "
        "(fork_id, parent_session_id, child_session_id, fork_kind, "
        "fork_reason, initiated_by, bootstrap_context_role_internal, created_at) "
        "VALUES (?, ?, ?, 'branch', '', 'user', 'fork_context', 0)",
        (fork_id, "parent-sess", "child-sess"),
    )
    db.commit()
    return fork_id


def test_create_replay_record(db: sqlite3.Connection) -> None:
    repo = ReplayRecordRepository(db)
    fork_id = _seed_fork(db)
    rec = repo.create(
        source_session_id="sess-1",
        fork_id=fork_id,
        replay_scope="full",
        outcome="completed",
    )
    assert isinstance(rec, ReplayRecord)
    assert rec.replay_id
    assert rec.source_session_id == "sess-1"
    assert rec.fork_id == fork_id
    assert rec.replay_scope == "full"
    assert rec.equivalence_rule == "final_state_plus_reviewer_judgment"
    assert rec.outcome == "completed"
    assert rec.created_at > 0


def test_create_replay_record_with_checkpoint(db: sqlite3.Connection) -> None:
    repo = ReplayRecordRepository(db)
    fork_id = _seed_fork(db)
    rec = repo.create(
        source_session_id="sess-1",
        fork_id=fork_id,
        checkpoint={"state": "ready", "step": 5},
        equivalence_rule="exact_match",
        outcome="diverged",
    )
    assert rec.checkpoint == {"state": "ready", "step": 5}
    assert rec.equivalence_rule == "exact_match"
    assert rec.outcome == "diverged"


def test_get_by_id(db: sqlite3.Connection) -> None:
    repo = ReplayRecordRepository(db)
    fork_id = _seed_fork(db)
    rec = repo.create(
        source_session_id="sess-1",
        fork_id=fork_id,
    )
    fetched = repo.get_by_id(rec.replay_id)
    assert fetched is not None
    assert fetched.replay_id == rec.replay_id
    assert repo.get_by_id("nonexistent") is None


def test_list_by_session(db: sqlite3.Connection) -> None:
    repo = ReplayRecordRepository(db)
    fork_id1 = _seed_fork(db)
    fork_id2 = _seed_fork(db)
    fork_id3 = _seed_fork(db)
    repo.create(source_session_id="sess-1", fork_id=fork_id1)
    repo.create(source_session_id="sess-1", fork_id=fork_id2)
    repo.create(source_session_id="sess-2", fork_id=fork_id3)
    results = repo.list_by_session("sess-1")
    assert len(results) == 2
    assert all(r.source_session_id == "sess-1" for r in results)


def test_delete_replay_record(db: sqlite3.Connection) -> None:
    repo = ReplayRecordRepository(db)
    fork_id = _seed_fork(db)
    rec = repo.create(
        source_session_id="sess-1",
        fork_id=fork_id,
    )
    assert repo.delete(rec.replay_id) is True
    assert repo.get_by_id(rec.replay_id) is None
    assert repo.delete("nonexistent") is False
