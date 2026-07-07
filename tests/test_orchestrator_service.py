"""Tests for OrchestratorService."""

import json

import pytest

from agent_workbench.models.agent_profile import AgentProfileRepository
from agent_workbench.models.channel import Channel
from agent_workbench.models.routed_message import RoutedMessage
from agent_workbench.models.task_spec import TaskSpecRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.orchestrator_service import (
    ChannelNotFoundError,
    OrchestratorService,
    SOURCE_TARGET_ORCHESTRATOR,
    SOURCE_TARGET_WORKER,
)
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.session_service import SessionService


@pytest.fixture
def workspace_id(db):
    return WorkspaceRepository(db).create(tenant_id="t1", name="WS").workspace_id


@pytest.fixture
def session_svc(db):
    return SessionService(db)


@pytest.fixture
def profile_svc(db):
    return ProfileService(db)


@pytest.fixture
def task_spec_repo(db):
    return TaskSpecRepository(db)


@pytest.fixture
def profile_repo(db):
    return AgentProfileRepository(db)


@pytest.fixture
def orch(db):
    return OrchestratorService(db)


# ---------------------------------------------------------------------
# Worker dispatch
# ---------------------------------------------------------------------


class TestDispatchWorker:
    def test_dispatch_creates_binding(
        self, orch, session_svc, profile_svc, workspace_id
    ):
        s = session_svc.create_session(
            workspace_id=workspace_id, session_type="work"
        )
        p = profile_svc.create_profile(name="impl", function="implementer")
        binding = orch.dispatch_worker(s.session_id, p.agent_profile_id)
        assert binding.session_id == s.session_id
        assert binding.agent_profile_id == p.agent_profile_id
        assert binding.created_from == "initial"

    def test_dispatch_with_task_spec(
        self, orch, session_svc, profile_svc, workspace_id, task_spec_repo
    ):
        s = session_svc.create_session(
            workspace_id=workspace_id, session_type="work"
        )
        ts = task_spec_repo.create(
            workspace_id=workspace_id, objective="do the thing"
        )
        p = profile_svc.create_profile(name="impl", function="implementer")
        orch.dispatch_worker(
            s.session_id, p.agent_profile_id, task_spec_id=ts.task_spec_id
        )
        reloaded = session_svc.get_session(s.session_id)
        assert reloaded.task_spec_id == ts.task_spec_id


# ---------------------------------------------------------------------
# Mediate worker communication
# ---------------------------------------------------------------------


class TestMediateWorkerCommunication:
    def _setup_workers(
        self, session_svc, profile_svc, orch, workspace_id, channel_repo
    ):
        # Two distinct sessions, each with a profile, each in their own
        # channel so the routed messages have a valid channel_id.
        ch1 = channel_repo.create(
            workspace_id=workspace_id, channel_kind="chat", title="A"
        )
        ch2 = channel_repo.create(
            workspace_id=workspace_id, channel_kind="chat", title="B"
        )
        s1 = session_svc.create_session(
            workspace_id=workspace_id, session_type="chat", channel_id=ch1.channel_id
        )
        s2 = session_svc.create_session(
            workspace_id=workspace_id, session_type="chat", channel_id=ch2.channel_id
        )
        p1 = profile_svc.create_profile(name="a-worker")
        p2 = profile_svc.create_profile(name="b-worker")
        b1 = orch.dispatch_worker(s1.session_id, p1.agent_profile_id)
        b2 = orch.dispatch_worker(s2.session_id, p2.agent_profile_id)
        return b1, b2, ch1, ch2

    def test_mediate_writes_two_legs(
        self, orch, session_svc, profile_svc, workspace_id, db
    ):
        from agent_workbench.models.channel import ChannelRepository

        channel_repo = ChannelRepository(db)
        b1, b2, ch1, ch2 = self._setup_workers(
            session_svc, profile_svc, orch, workspace_id, channel_repo
        )
        out = orch.mediate_worker_communication(
            source_worker_id=b1.binding_id,
            target_worker_id=b2.binding_id,
            message={"hello": "world", "n": 1},
        )
        assert isinstance(out, RoutedMessage)
        # Downlink goes from orchestrator -> target worker
        assert out.source_type == SOURCE_TARGET_ORCHESTRATOR
        assert out.source_id == "orchestrator"
        assert out.target_type == SOURCE_TARGET_WORKER
        assert out.target_id == b2.binding_id
        assert out.message_kind == "conversation"
        # Payload envelope
        assert out.payload_ref is not None
        envelope = json.loads(out.payload_ref)
        assert envelope["envelope"] == "orchestrator_mediated"
        assert envelope["message"] == {"hello": "world", "n": 1}

        # Uplink row should exist and target the orchestrator
        uplinks = orch.messages.list_by_target(
            SOURCE_TARGET_ORCHESTRATOR, "orchestrator"
        )
        assert any(
            r.source_id == b1.binding_id and r.target_type == SOURCE_TARGET_ORCHESTRATOR
            for r in uplinks
        )

    def test_mediate_uses_channel_for_envelope(
        self, orch, session_svc, profile_svc, workspace_id, db
    ):
        from agent_workbench.models.channel import ChannelRepository

        channel_repo = ChannelRepository(db)
        b1, b2, ch1, ch2 = self._setup_workers(
            session_svc, profile_svc, orch, workspace_id, channel_repo
        )
        out = orch.mediate_worker_communication(
            source_worker_id=b1.binding_id,
            target_worker_id=b2.binding_id,
            message={"k": "v"},
        )
        # The downlink row should sit in one of the workspace's channels
        assert out.channel_id in (ch1.channel_id, ch2.channel_id)

    def test_mediate_to_self_rejected(
        self, orch, session_svc, profile_svc, workspace_id, db
    ):
        from agent_workbench.models.channel import ChannelRepository

        channel_repo = ChannelRepository(db)
        b1, b2, ch1, ch2 = self._setup_workers(
            session_svc, profile_svc, orch, workspace_id, channel_repo
        )
        with pytest.raises(ValueError, match="to itself"):
            orch.mediate_worker_communication(
                source_worker_id=b1.binding_id,
                target_worker_id=b1.binding_id,
                message={"x": 1},
            )

    def test_mediate_non_dict_message_rejected(
        self, orch, session_svc, profile_svc, workspace_id, db
    ):
        from agent_workbench.models.channel import ChannelRepository

        channel_repo = ChannelRepository(db)
        b1, b2, ch1, ch2 = self._setup_workers(
            session_svc, profile_svc, orch, workspace_id, channel_repo
        )
        with pytest.raises(TypeError, match="must be a dict"):
            orch.mediate_worker_communication(
                source_worker_id=b1.binding_id,
                target_worker_id=b2.binding_id,
                message="not a dict",
            )

    def test_mediate_unknown_source_worker_raises(
        self, orch, session_svc, profile_svc, workspace_id, db
    ):
        from agent_workbench.models.channel import ChannelRepository

        channel_repo = ChannelRepository(db)
        b1, b2, ch1, ch2 = self._setup_workers(
            session_svc, profile_svc, orch, workspace_id, channel_repo
        )
        with pytest.raises(LookupError):
            orch.mediate_worker_communication(
                source_worker_id="not-a-binding",
                target_worker_id=b2.binding_id,
                message={"x": 1},
            )

    def test_mediate_records_no_direct_worker_to_worker_row(
        self, orch, session_svc, profile_svc, workspace_id, db
    ):
        """No row in routed_messages should have worker->worker directly."""
        from agent_workbench.models.channel import ChannelRepository

        channel_repo = ChannelRepository(db)
        b1, b2, ch1, ch2 = self._setup_workers(
            session_svc, profile_svc, orch, workspace_id, channel_repo
        )
        orch.mediate_worker_communication(
            source_worker_id=b1.binding_id,
            target_worker_id=b2.binding_id,
            message={"x": 1},
        )
        # Iterate every message in the workspace and assert none of them
        # is source=worker -> target=worker.
        for ch in (ch1, ch2):
            for r in orch.messages.list_by_channel(ch.channel_id):
                assert not (
                    r.source_type == SOURCE_TARGET_WORKER
                    and r.target_type == SOURCE_TARGET_WORKER
                ), f"Found direct worker->worker row: {r}"


# ---------------------------------------------------------------------
# Channel management
# ---------------------------------------------------------------------


class TestChannelOps:
    def test_create_channel(self, orch, workspace_id):
        ch = orch.create_channel(
            workspace_id=workspace_id, channel_kind="research", title="R"
        )
        assert isinstance(ch, Channel)
        assert ch.workspace_id == workspace_id
        assert ch.channel_kind == "research"
        assert ch.title == "R"
        assert ch.status == "active"

    def test_create_channel_invalid_kind_raises(self, orch, workspace_id):
        with pytest.raises(ValueError, match="Invalid channel_kind"):
            orch.create_channel(
                workspace_id=workspace_id, channel_kind="bogus"
            )

    def test_get_channel(self, orch, workspace_id):
        created = orch.create_channel(
            workspace_id=workspace_id, channel_kind="work", title="W"
        )
        fetched = orch.get_channel(created.channel_id)
        assert fetched.channel_id == created.channel_id

    def test_get_channel_missing_raises(self, orch):
        with pytest.raises(ChannelNotFoundError):
            orch.get_channel("nope")

    def test_list_channels_empty(self, orch, workspace_id):
        assert orch.list_channels(workspace_id) == []

    def test_list_channels_returns_all(self, orch, workspace_id):
        orch.create_channel(workspace_id=workspace_id, channel_kind="chat", title="c1")
        orch.create_channel(workspace_id=workspace_id, channel_kind="research", title="r1")
        orch.create_channel(workspace_id=workspace_id, channel_kind="work", title="w1")
        listed = orch.list_channels(workspace_id)
        kinds = sorted(c.channel_kind for c in listed)
        assert kinds == ["chat", "research", "work"]
