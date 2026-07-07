"""Tests for RoutedMessageRepository."""

import pytest

from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.routed_message import (
    RoutedMessage,
    RoutedMessageRepository,
)
from agent_workbench.models.workspace import WorkspaceRepository


@pytest.fixture
def repo(db):
    return RoutedMessageRepository(db)


@pytest.fixture
def ws_repo(db):
    return WorkspaceRepository(db)


@pytest.fixture
def ch_repo(db):
    return ChannelRepository(db)


@pytest.fixture
def workspace(ws_repo):
    return ws_repo.create(tenant_id="tenant-1", name="Test Workspace")


@pytest.fixture
def channel(ch_repo, workspace):
    return ch_repo.create(
        workspace_id=workspace.workspace_id,
        channel_kind="chat",
        title="Test Channel",
    )


# ------------------------------------------------------------------
# Create
# ------------------------------------------------------------------


class TestRoutedMessageCreate:
    def test_create_returns_dataclass(self, repo, channel):
        rm = repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        assert isinstance(rm, RoutedMessage)
        assert rm.workspace_id == channel.workspace_id
        assert rm.channel_id == channel.channel_id
        assert rm.source_type == "agent"
        assert rm.source_id == "agent-1"
        assert rm.target_type == "agent"
        assert rm.target_id == "agent-2"
        assert rm.message_kind == "conversation"
        assert rm.routed_message_id is not None
        assert rm.created_at > 0

    def test_create_generates_unique_ids(self, repo, channel):
        rm1 = repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        rm2 = repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="dispatch",
        )
        assert rm1.routed_message_id != rm2.routed_message_id

    def test_create_with_session_id(self, repo, channel):
        rm = repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            session_id="session-1",
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        assert rm.session_id == "session-1"

    def test_create_with_payload_ref(self, repo, channel):
        rm = repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="report",
            payload_ref="/path/to/payload.json",
        )
        assert rm.payload_ref == "/path/to/payload.json"

    def test_create_all_message_kinds(self, repo, channel):
        kinds = [
            "conversation",
            "dispatch",
            "steering",
            "report",
            "system",
            "telemetry",
        ]
        for kind in kinds:
            rm = repo.create(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type="agent",
                source_id="agent-1",
                target_type="agent",
                target_id="agent-2",
                message_kind=kind,
            )
            assert rm.message_kind == kind


# ------------------------------------------------------------------
# Get by ID
# ------------------------------------------------------------------


class TestRoutedMessageGetById:
    def test_get_existing(self, repo, channel):
        rm = repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        found = repo.get_by_id(rm.routed_message_id)
        assert found is not None
        assert found.routed_message_id == rm.routed_message_id
        assert found.source_type == "agent"

    def test_get_nonexistent(self, repo):
        assert repo.get_by_id("nonexistent") is None


# ------------------------------------------------------------------
# List by channel
# ------------------------------------------------------------------


class TestRoutedMessageListByChannel:
    def test_list_empty(self, repo, channel):
        assert repo.list_by_channel(channel.channel_id) == []

    def test_list_multiple(self, repo, channel):
        repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-3",
            message_kind="dispatch",
        )
        msgs = repo.list_by_channel(channel.channel_id)
        assert len(msgs) == 2
        assert all(isinstance(m, RoutedMessage) for m in msgs)

    def test_list_isolated_to_channel(self, repo, ch_repo, workspace):
        ch1 = ch_repo.create(
            workspace_id=workspace.workspace_id,
            channel_kind="chat",
            title="Channel A",
        )
        ch2 = ch_repo.create(
            workspace_id=workspace.workspace_id,
            channel_kind="chat",
            title="Channel B",
        )
        repo.create(
            workspace_id=ch1.workspace_id,
            channel_id=ch1.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        repo.create(
            workspace_id=ch2.workspace_id,
            channel_id=ch2.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        assert len(repo.list_by_channel(ch1.channel_id)) == 1
        assert len(repo.list_by_channel(ch2.channel_id)) == 1


# ------------------------------------------------------------------
# List by session
# ------------------------------------------------------------------


class TestRoutedMessageListBySession:
    def test_list_empty(self, repo):
        assert repo.list_by_session("session-nonexistent") == []

    def test_list_multiple(self, repo, channel):
        repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            session_id="session-1",
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            session_id="session-1",
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-3",
            message_kind="dispatch",
        )
        msgs = repo.list_by_session("session-1")
        assert len(msgs) == 2

    def test_list_isolated_to_session(self, repo, channel):
        repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            session_id="session-1",
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            session_id="session-2",
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        assert len(repo.list_by_session("session-1")) == 1
        assert len(repo.list_by_session("session-2")) == 1


# ------------------------------------------------------------------
# List by target
# ------------------------------------------------------------------


class TestRoutedMessageListByTarget:
    def test_list_empty(self, repo):
        assert repo.list_by_target("agent", "agent-nonexistent") == []

    def test_list_multiple(self, repo, channel):
        repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-3",
            target_type="agent",
            target_id="agent-2",
            message_kind="dispatch",
        )
        msgs = repo.list_by_target("agent", "agent-2")
        assert len(msgs) == 2

    def test_list_isolated_to_target(self, repo, channel):
        repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-3",
            message_kind="conversation",
        )
        assert len(repo.list_by_target("agent", "agent-2")) == 1
        assert len(repo.list_by_target("agent", "agent-3")) == 1


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------


class TestRoutedMessageDelete:
    def test_delete_existing(self, repo, channel):
        rm = repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        assert repo.delete(rm.routed_message_id) is True
        assert repo.get_by_id(rm.routed_message_id) is None

    def test_delete_nonexistent(self, repo):
        assert repo.delete("nonexistent") is False
