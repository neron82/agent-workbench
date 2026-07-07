"""Tests for ForkRecordRepository."""

import json
import sqlite3

from agent_workbench.models.fork_record import ForkRecord, ForkRecordRepository


def test_create_and_get_by_id(db: sqlite3.Connection):
    repo = ForkRecordRepository(db)
    record = repo.create(
        parent_session_id="parent-abc",
        child_session_id="child-def",
        fork_kind="branch",
        fork_reason="exploring alternative",
        initiated_by="user",
    )
    assert record.fork_id is not None
    assert record.parent_session_id == "parent-abc"
    assert record.child_session_id == "child-def"
    assert record.fork_kind == "branch"
    assert record.bootstrap_context_role_internal == "fork_context"

    fetched = repo.get_by_id(record.fork_id)
    assert fetched is not None
    assert fetched.fork_id == record.fork_id
    assert fetched.fork_reason == "exploring alternative"


def test_create_with_json_fields(db: sqlite3.Connection):
    repo = ForkRecordRepository(db)
    record = repo.create(
        parent_session_id="p1",
        child_session_id="c1",
        fork_kind="replay",
        decisions_json={"key": "value"},
        assumptions_json={"a": 1},
        open_questions_json=["q1"],
        relevant_artifacts_json={"art": "x"},
        checkpoint_json={"step": 5},
    )
    assert record.decisions_json == {"key": "value"}
    assert record.checkpoint_json == {"step": 5}

    fetched = repo.get_by_id(record.fork_id)
    assert fetched is not None
    assert fetched.decisions_json == {"key": "value"}
    assert fetched.checkpoint_json == {"step": 5}


def test_create_default_bootstrap_context_role(db: sqlite3.Connection):
    repo = ForkRecordRepository(db)
    record = repo.create(
        parent_session_id="p1",
        child_session_id="c1",
        fork_kind="branch",
    )
    assert record.bootstrap_context_role_internal == "fork_context"


def test_create_custom_bootstrap_context_role(db: sqlite3.Connection):
    repo = ForkRecordRepository(db)
    record = repo.create(
        parent_session_id="p1",
        child_session_id="c1",
        fork_kind="branch",
        bootstrap_context_role_internal="custom_role",
    )
    assert record.bootstrap_context_role_internal == "custom_role"


def test_get_by_child_session(db: sqlite3.Connection):
    repo = ForkRecordRepository(db)
    record = repo.create(
        parent_session_id="p1",
        child_session_id="child-unique",
        fork_kind="branch",
    )
    found = repo.get_by_child_session("child-unique")
    assert found is not None
    assert found.fork_id == record.fork_id

    assert repo.get_by_child_session("nonexistent") is None


def test_get_by_parent_session(db: sqlite3.Connection):
    repo = ForkRecordRepository(db)
    repo.create(
        parent_session_id="parent-x",
        child_session_id="c1",
        fork_kind="branch",
    )
    repo.create(
        parent_session_id="parent-x",
        child_session_id="c2",
        fork_kind="retry",
    )
    results = repo.get_by_parent_session("parent-x")
    assert len(results) == 2
    assert all(r.parent_session_id == "parent-x" for r in results)


def test_list_by_kind(db: sqlite3.Connection):
    repo = ForkRecordRepository(db)
    repo.create(parent_session_id="p1", child_session_id="c1", fork_kind="branch")
    repo.create(parent_session_id="p2", child_session_id="c2", fork_kind="branch")
    repo.create(parent_session_id="p3", child_session_id="c3", fork_kind="replay")

    branches = repo.list_by_kind("branch")
    assert len(branches) == 2
    replays = repo.list_by_kind("replay")
    assert len(replays) == 1
    assert len(repo.list_by_kind("type_change")) == 0


def test_delete(db: sqlite3.Connection):
    repo = ForkRecordRepository(db)
    record = repo.create(
        parent_session_id="p1",
        child_session_id="c1",
        fork_kind="branch",
    )
    assert repo.delete(record.fork_id) is True
    assert repo.get_by_id(record.fork_id) is None
    assert repo.delete(record.fork_id) is False


def test_get_by_id_missing(db: sqlite3.Connection):
    repo = ForkRecordRepository(db)
    assert repo.get_by_id("nonexistent") is None
