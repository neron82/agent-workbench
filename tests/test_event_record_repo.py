"""Tests for EventRecordRepository."""

import pytest

from agent_workbench.models.event_record import EventRecord, EventRecordRepository
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.routed_message import RoutedMessageRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.models.channel import ChannelRepository


@pytest.fixture
def repo(db):
    return EventRecordRepository(db)


@pytest.fixture
def hr_repo(db):
    return HarnessRunRepository(db)


@pytest.fixture
def rm_repo(db):
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
def harness_run(hr_repo, workspace):
    return hr_repo.create(
        workspace_id=workspace.workspace_id,
        session_id="session-1",
        harness_type="discussion",
    )


@pytest.fixture
def channel(ch_repo, workspace):
    return ch_repo.create(
        workspace_id=workspace.workspace_id,
        channel_kind="chat",
        title="Test Channel",
    )


@pytest.fixture
def routed_message(rm_repo, channel):
    return rm_repo.create(
        workspace_id=channel.workspace_id,
        channel_id=channel.channel_id,
        source_type="agent",
        source_id="agent-1",
        target_type="agent",
        target_id="agent-2",
        message_kind="conversation",
    )


# ------------------------------------------------------------------
# Create
# ------------------------------------------------------------------


class TestEventRecordCreate:
    def test_create_returns_dataclass(self, repo):
        ev = repo.create(
            event_type="start",
            event_source="harness-runner",
        )
        assert isinstance(ev, EventRecord)
        assert ev.event_type == "start"
        assert ev.event_source == "harness-runner"
        assert ev.event_id is not None
        assert ev.event_ts > 0

    def test_create_generates_unique_ids(self, repo):
        ev1 = repo.create(event_type="start", event_source="src-1")
        ev2 = repo.create(event_type="end", event_source="src-2")
        assert ev1.event_id != ev2.event_id

    def test_create_with_harness_run(self, repo, harness_run):
        ev = repo.create(
            event_type="status_change",
            event_source="orchestrator",
            harness_run_id=harness_run.harness_run_id,
        )
        assert ev.harness_run_id == harness_run.harness_run_id

    def test_create_with_routed_message(self, repo, routed_message):
        ev = repo.create(
            event_type="message_sent",
            event_source="router",
            routed_message_id=routed_message.routed_message_id,
        )
        assert ev.routed_message_id == routed_message.routed_message_id

    def test_create_with_payload_ref(self, repo):
        ev = repo.create(
            event_type="artifact_created",
            event_source="builder",
            event_payload_ref="/path/to/artifact.json",
        )
        assert ev.event_payload_ref == "/path/to/artifact.json"

    def test_create_with_custom_ts(self, repo):
        ev = repo.create(
            event_type="custom",
            event_source="test",
            event_ts=12345.67,
        )
        assert ev.event_ts == 12345.67


# ------------------------------------------------------------------
# Get by ID
# ------------------------------------------------------------------


class TestEventRecordGetById:
    def test_get_existing(self, repo):
        ev = repo.create(event_type="start", event_source="src-1")
        found = repo.get_by_id(ev.event_id)
        assert found is not None
        assert found.event_id == ev.event_id
        assert found.event_type == "start"

    def test_get_nonexistent(self, repo):
        assert repo.get_by_id("nonexistent") is None


# ------------------------------------------------------------------
# List by harness run
# ------------------------------------------------------------------


class TestEventRecordListByHarnessRun:
    def test_list_empty(self, repo, harness_run):
        assert repo.list_by_harness_run(harness_run.harness_run_id) == []

    def test_list_multiple(self, repo, harness_run):
        repo.create(
            event_type="start",
            event_source="runner",
            harness_run_id=harness_run.harness_run_id,
        )
        repo.create(
            event_type="end",
            event_source="runner",
            harness_run_id=harness_run.harness_run_id,
        )
        events = repo.list_by_harness_run(harness_run.harness_run_id)
        assert len(events) == 2
        assert all(isinstance(e, EventRecord) for e in events)

    def test_list_isolated_to_harness_run(self, repo, hr_repo, workspace):
        hr1 = hr_repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="discussion",
        )
        hr2 = hr_repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-2",
            harness_type="hermes",
        )
        repo.create(
            event_type="start",
            event_source="runner",
            harness_run_id=hr1.harness_run_id,
        )
        repo.create(
            event_type="start",
            event_source="runner",
            harness_run_id=hr2.harness_run_id,
        )
        assert len(repo.list_by_harness_run(hr1.harness_run_id)) == 1
        assert len(repo.list_by_harness_run(hr2.harness_run_id)) == 1


# ------------------------------------------------------------------
# List by routed message
# ------------------------------------------------------------------


class TestEventRecordListByRoutedMessage:
    def test_list_empty(self, repo, routed_message):
        assert repo.list_by_routed_message(routed_message.routed_message_id) == []

    def test_list_multiple(self, repo, routed_message):
        repo.create(
            event_type="received",
            event_source="router",
            routed_message_id=routed_message.routed_message_id,
        )
        repo.create(
            event_type="processed",
            event_source="handler",
            routed_message_id=routed_message.routed_message_id,
        )
        events = repo.list_by_routed_message(routed_message.routed_message_id)
        assert len(events) == 2

    def test_list_isolated_to_routed_message(self, repo, rm_repo, channel):
        rm1 = rm_repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-2",
            message_kind="conversation",
        )
        rm2 = rm_repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type="agent",
            source_id="agent-1",
            target_type="agent",
            target_id="agent-3",
            message_kind="dispatch",
        )
        repo.create(
            event_type="received",
            event_source="router",
            routed_message_id=rm1.routed_message_id,
        )
        repo.create(
            event_type="received",
            event_source="router",
            routed_message_id=rm2.routed_message_id,
        )
        assert len(repo.list_by_routed_message(rm1.routed_message_id)) == 1
        assert len(repo.list_by_routed_message(rm2.routed_message_id)) == 1


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------


class TestEventRecordDelete:
    def test_delete_existing(self, repo):
        ev = repo.create(event_type="start", event_source="src-1")
        assert repo.delete(ev.event_id) is True
        assert repo.get_by_id(ev.event_id) is None

    def test_delete_nonexistent(self, repo):
        assert repo.delete("nonexistent") is False
