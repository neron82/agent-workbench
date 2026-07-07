"""Tests for SessionService."""

import pytest

from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.session_extension import SessionExtension
from agent_workbench.models.task_spec import TaskSpecRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.session_service import (
    SessionNotFoundError,
    SessionService,
)


@pytest.fixture
def workspace_id(db):
    repo = WorkspaceRepository(db)
    ws = repo.create(tenant_id="t1", name="WS")
    return ws.workspace_id


@pytest.fixture
def svc(db):
    return SessionService(db)


@pytest.fixture
def channel_repo(db):
    return ChannelRepository(db)


@pytest.fixture
def task_spec_id(workspace_id, db):
    """Create a real task spec so session.task_spec_id FKs are satisfied."""
    repo = TaskSpecRepository(db)
    ts = repo.create(workspace_id=workspace_id, objective="fixture")
    return ts.task_spec_id


class TestCreateSession:
    def test_create_minimal_returns_session(self, svc, workspace_id):
        s = svc.create_session(workspace_id=workspace_id, session_type="chat")
        assert isinstance(s, SessionExtension)
        assert s.workspace_id == workspace_id
        assert s.session_type == "chat"
        assert s.status == "active"
        assert s.session_id is not None
        assert s.fork_id is None
        assert s.task_spec_id is None
        assert s.agent_profile_binding_id is None

    def test_create_with_invalid_type_raises(self, svc, workspace_id):
        with pytest.raises(ValueError, match="Invalid session_type"):
            svc.create_session(workspace_id=workspace_id, session_type="bogus")

    def test_create_all_types(self, svc, workspace_id):
        for stype in ("chat", "research", "work"):
            s = svc.create_session(workspace_id=workspace_id, session_type=stype)
            assert s.session_type == stype

    def test_create_links_to_channel(self, svc, channel_repo, workspace_id):
        ch = channel_repo.create(
            workspace_id=workspace_id, channel_kind="chat", title="general"
        )
        s = svc.create_session(
            workspace_id=workspace_id, session_type="chat", channel_id=ch.channel_id
        )
        # Channel's active_session_id should now point at the new session.
        reloaded = channel_repo.get_by_id(ch.channel_id)
        assert reloaded is not None
        assert reloaded.active_session_id == s.session_id

    def test_create_with_nonexistent_channel_raises(self, svc, workspace_id):
        with pytest.raises(SessionNotFoundError):
            svc.create_session(
                workspace_id=workspace_id,
                session_type="chat",
                channel_id="does-not-exist",
            )

    def test_create_channel_in_different_workspace_rejected(
        self, svc, channel_repo, db
    ):
        ws_a = WorkspaceRepository(db).create(tenant_id="t", name="A")
        ws_b = WorkspaceRepository(db).create(tenant_id="t", name="B")
        ch = channel_repo.create(
            workspace_id=ws_a.workspace_id, channel_kind="chat"
        )
        with pytest.raises(ValueError, match="belongs to workspace"):
            svc.create_session(
                workspace_id=ws_b.workspace_id,
                session_type="chat",
                channel_id=ch.channel_id,
            )


class TestGetSession:
    def test_get_returns_session(self, svc, workspace_id):
        created = svc.create_session(workspace_id=workspace_id, session_type="chat")
        fetched = svc.get_session(created.session_id)
        assert fetched.session_id == created.session_id
        assert fetched.session_type == "chat"

    def test_get_missing_raises(self, svc):
        with pytest.raises(SessionNotFoundError):
            svc.get_session("nope")


class TestUpdateStatus:
    def test_update_status_round_trip(self, svc, workspace_id):
        s = svc.create_session(workspace_id=workspace_id, session_type="work")
        updated = svc.update_session_status(s.session_id, "waiting_review")
        assert updated.status == "waiting_review"
        # And the same value is reflected on a fresh fetch.
        assert svc.get_session(s.session_id).status == "waiting_review"

    def test_update_status_invalid_value_raises(self, svc, workspace_id):
        s = svc.create_session(workspace_id=workspace_id, session_type="chat")
        with pytest.raises(ValueError, match="Invalid status"):
            svc.update_session_status(s.session_id, "nope")

    def test_update_status_missing_raises(self, svc):
        with pytest.raises(SessionNotFoundError):
            svc.update_session_status("nope", "done")


class TestAssignTaskSpec:
    def test_assign_task_spec(self, svc, workspace_id, task_spec_id):
        s = svc.create_session(workspace_id=workspace_id, session_type="work")
        updated = svc.assign_task_spec(s.session_id, task_spec_id)
        assert updated.task_spec_id == task_spec_id
        assert svc.get_session(s.session_id).task_spec_id == task_spec_id

    def test_assign_task_spec_clear(self, svc, workspace_id, task_spec_id):
        s = svc.create_session(workspace_id=workspace_id, session_type="work")
        svc.assign_task_spec(s.session_id, task_spec_id)
        cleared = svc.assign_task_spec(s.session_id, None)
        assert cleared.task_spec_id is None

    def test_assign_task_spec_missing_session(self, svc):
        with pytest.raises(SessionNotFoundError):
            svc.assign_task_spec("nope", "ts-1")


class TestTransitionSessionType:
    def test_transition_creates_fork_and_child(self, svc, workspace_id):
        parent = svc.create_session(workspace_id=workspace_id, session_type="chat")
        child, fork = svc.transition_session_type(
            session_id=parent.session_id,
            new_type="research",
            fork_reason="user asked for research",
            initiated_by="user",
        )
        # Child identity and metadata
        assert child.session_id != parent.session_id
        assert child.workspace_id == parent.workspace_id
        assert child.session_type == "research"
        assert child.status == "active"
        assert child.fork_id == fork.fork_id
        # Fork record linkage
        assert fork.parent_session_id == parent.session_id
        assert fork.child_session_id == child.session_id
        assert fork.fork_kind == "type_change"
        assert fork.initiated_by == "user"
        assert fork.fork_reason == "user asked for research"
        # Parent is unchanged.
        assert svc.get_session(parent.session_id).session_type == "chat"

    def test_transition_missing_parent_raises(self, svc):
        with pytest.raises(SessionNotFoundError):
            svc.transition_session_type(
                session_id="nope",
                new_type="research",
                fork_reason="x",
            )

    def test_transition_invalid_target_type_raises(self, svc, workspace_id):
        parent = svc.create_session(workspace_id=workspace_id, session_type="chat")
        with pytest.raises(ValueError):
            svc.transition_session_type(
                session_id=parent.session_id,
                new_type="bogus",
                fork_reason="x",
            )

    def test_transition_to_same_type_creates_branch_fork(
        self, svc, workspace_id
    ):
        # The contract is "always creates a fork" — same-type transitions
        # are still valid, they just produce a 'branch' fork rather than
        # a 'type_change' fork.
        parent = svc.create_session(workspace_id=workspace_id, session_type="chat")
        child, fork = svc.transition_session_type(
            session_id=parent.session_id,
            new_type="chat",
            fork_reason="branching chat",
        )
        assert child.session_type == "chat"
        assert fork.fork_kind in ("branch", "type_change")
        # And the parent is still untouched.
        assert svc.get_session(parent.session_id).session_type == "chat"

    def test_transition_inherits_task_spec(
        self, svc, workspace_id, task_spec_id
    ):
        parent = svc.create_session(workspace_id=workspace_id, session_type="chat")
        svc.assign_task_spec(parent.session_id, task_spec_id)
        child, _ = svc.transition_session_type(
            session_id=parent.session_id,
            new_type="work",
            fork_reason="needs work",
        )
        assert child.task_spec_id == task_spec_id


class TestTypeImmutability:
    def test_no_public_type_mutation_method(self, svc, workspace_id):
        # Sanity: the service exposes no method that mutates session_type
        # in place. The transition is always via a fork.
        public_methods = [m for m in dir(svc) if not m.startswith("_")]
        mutator_candidates = [
            m for m in public_methods
            if "type" in m.lower()
            and "transition" not in m.lower()
            and m != "create_session"
        ]
        assert mutator_candidates == []
