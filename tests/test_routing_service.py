"""Tests for RoutingService — message routing, addressing, and anti-chatter."""

from __future__ import annotations

import pytest

from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.event_record import EventRecord
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.routed_message import RoutedMessage
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.routing_service import (
    SOURCE_TYPE_ORCHESTRATOR,
    SOURCE_TYPE_USER,
    SOURCE_TYPE_WORKER,
    TARGET_TYPE_AGENT,
    TARGET_TYPE_ALL,
    TARGET_TYPE_ORCHESTRATOR,
    TARGET_TYPE_SYSTEM,
    RoutingService,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def ws_repo(db):
    return WorkspaceRepository(db)


@pytest.fixture
def ch_repo(db):
    return ChannelRepository(db)


@pytest.fixture
def hr_repo(db):
    return HarnessRunRepository(db)


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


@pytest.fixture
def harness_run(hr_repo, workspace):
    return hr_repo.create(
        workspace_id=workspace.workspace_id,
        session_id="session-1",
        harness_type="opencode",
    )


@pytest.fixture
def router(db):
    return RoutingService(db)


# ------------------------------------------------------------------
# route_message — source/target metadata
# ------------------------------------------------------------------


class TestRouteMessageBasics:
    def test_route_message_creates_routed_message_with_source_and_target(
        self, router, channel
    ):
        rm = router.route_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_USER,
            source_id="user-1",
            target_type=TARGET_TYPE_ORCHESTRATOR,
            target_id="@orchestrator",
            message_kind="conversation",
        )
        assert isinstance(rm, RoutedMessage)
        assert rm.workspace_id == channel.workspace_id
        assert rm.channel_id == channel.channel_id
        assert rm.source_type == "user"
        assert rm.source_id == "user-1"
        assert rm.target_type == "orchestrator"
        assert rm.target_id == "@orchestrator"
        assert rm.message_kind == "conversation"
        assert rm.routed_message_id is not None
        assert rm.created_at > 0

    def test_route_message_persists_session_id_and_payload_ref(
        self, router, channel
    ):
        rm = router.route_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_USER,
            source_id="user-1",
            target_type=TARGET_TYPE_ORCHESTRATOR,
            target_id="@orchestrator",
            message_kind="conversation",
            session_id="session-1",
            payload_ref="/path/to/payload.json",
        )
        assert rm.session_id == "session-1"
        assert rm.payload_ref == "/path/to/payload.json"

    def test_route_message_rejects_null_source_type(self, router, channel):
        with pytest.raises(ValueError, match="source_type"):
            router.route_message(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type="",
                source_id="user-1",
                target_type=TARGET_TYPE_ORCHESTRATOR,
                target_id="@orchestrator",
                message_kind="conversation",
            )

    def test_route_message_rejects_null_source_id(self, router, channel):
        with pytest.raises(ValueError, match="source_id"):
            router.route_message(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type=SOURCE_TYPE_USER,
                source_id="",
                target_type=TARGET_TYPE_ORCHESTRATOR,
                target_id="@orchestrator",
                message_kind="conversation",
            )

    def test_route_message_rejects_null_target_type(self, router, channel):
        with pytest.raises(ValueError, match="target_type"):
            router.route_message(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type=SOURCE_TYPE_USER,
                source_id="user-1",
                target_type="",
                target_id="@orchestrator",
                message_kind="conversation",
            )

    def test_route_message_rejects_null_target_id(self, router, channel):
        with pytest.raises(ValueError, match="target_id"):
            router.route_message(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type=SOURCE_TYPE_USER,
                source_id="user-1",
                target_type=TARGET_TYPE_ORCHESTRATOR,
                target_id="",
                message_kind="conversation",
            )

    def test_route_message_rejects_invalid_message_kind(self, router, channel):
        with pytest.raises(ValueError, match="message_kind"):
            router.route_message(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type=SOURCE_TYPE_USER,
                source_id="user-1",
                target_type=TARGET_TYPE_ORCHESTRATOR,
                target_id="@orchestrator",
                message_kind="nonsense",
            )


# ------------------------------------------------------------------
# Default routing: user -> orchestrator
# ------------------------------------------------------------------


class TestDefaultRouting:
    def test_default_routing_user_to_orchestrator(self, router, channel):
        rm = router.route_default_user_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            user_id="user-1",
        )
        assert rm.source_type == "user"
        assert rm.source_id == "user-1"
        assert rm.target_type == "orchestrator"
        assert rm.target_id == "@orchestrator"
        assert rm.message_kind == "conversation"

    def test_orchestrator_to_worker_dispatch_works(self, router, channel):
        rm = router.route_orchestrator_dispatch(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            orchestrator_id="orch-1",
            worker_id="worker-1",
        )
        assert rm.source_type == "orchestrator"
        assert rm.source_id == "orch-1"
        assert rm.target_type == "worker"
        assert rm.target_id == "worker-1"
        assert rm.message_kind == "dispatch"

    def test_user_to_worker_without_explicit_dispatch_is_rejected(
        self, router, channel
    ):
        with pytest.raises(ValueError, match="user -> orchestrator"):
            router.route_message(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type=SOURCE_TYPE_USER,
                source_id="user-1",
                target_type=SOURCE_TYPE_WORKER,
                target_id="worker-1",
                message_kind="dispatch",
            )

    def test_user_to_worker_with_explicit_dispatch_is_allowed(
        self, router, channel
    ):
        rm = router.route_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_USER,
            source_id="user-1",
            target_type=SOURCE_TYPE_WORKER,
            target_id="worker-1",
            message_kind="dispatch",
            explicit_dispatch=True,
        )
        assert rm.source_type == "user"
        assert rm.target_type == "worker"


# ------------------------------------------------------------------
# Addressing — @all
# ------------------------------------------------------------------


class TestAddressingAtAll:
    def test_at_all_from_user_targets_orchestrator_only(self, router, channel):
        rm = router.route_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_USER,
            source_id="user-1",
            target_type=TARGET_TYPE_ALL,
            target_id="@all",
            message_kind="conversation",
        )
        assert rm.target_type == "all"
        assert rm.target_id == "@all"

    def test_at_all_from_orchestrator_targets_agents(self, router, channel):
        rm = router.route_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_ORCHESTRATOR,
            source_id="orch-1",
            target_type=TARGET_TYPE_ALL,
            target_id="@all",
            message_kind="conversation",
        )
        assert rm.target_type == "all"
        assert rm.source_type == "orchestrator"

    def test_at_all_rejects_execution_worker_source(self, router, channel):
        """Decision 7: @all targets active non-execution discussion
        participants only.  Routing from a worker via @all is rejected."""
        with pytest.raises(ValueError, match="@all"):
            router.route_message(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type=SOURCE_TYPE_WORKER,
                source_id="worker-1",
                target_type=TARGET_TYPE_ALL,
                target_id="@all",
                message_kind="conversation",
            )

    def test_at_all_does_not_target_execution_worker(self, router, channel):
        """@all routing should not produce a routed_message whose target is
        an execution worker.  The default user -> @all flow lands on
        target_type='all' / target_id='@all', never on a worker."""
        rm = router.route_default_user_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            user_id="user-1",
        )
        assert rm.target_type != SOURCE_TYPE_WORKER
        assert rm.target_id != "worker-1"


# ------------------------------------------------------------------
# Anti-chatter invariant — worker -> worker is rejected
# ------------------------------------------------------------------


class TestAntiChatter:
    def test_worker_to_worker_direct_message_raises_value_error(
        self, router, channel
    ):
        with pytest.raises(ValueError, match="worker"):
            router.route_message(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type=SOURCE_TYPE_WORKER,
                source_id="worker-1",
                target_type=SOURCE_TYPE_WORKER,
                target_id="worker-2",
                message_kind="conversation",
            )

    def test_worker_to_worker_via_agent_target_also_rejected(
        self, router, channel
    ):
        # Anti-chatter is enforced on source/target types, not on the
        # target_id alias.  A worker addressing another worker via the
        # 'agent' type is still a worker->worker hop.
        with pytest.raises(ValueError, match="worker"):
            router.route_message(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type=SOURCE_TYPE_WORKER,
                source_id="worker-1",
                target_type="agent",
                target_id="worker-2",
                message_kind="conversation",
            )

    def test_worker_to_orchestrator_is_allowed(self, router, channel):
        # Workers reporting back to the orchestrator is the normal channel
        # for inter-agent coordination and must work.
        rm = router.route_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_WORKER,
            source_id="worker-1",
            target_type=TARGET_TYPE_ORCHESTRATOR,
            target_id="@orchestrator",
            message_kind="report",
        )
        assert rm.source_type == "worker"
        assert rm.target_type == "orchestrator"
        assert rm.message_kind == "report"

    def test_orchestrator_to_worker_is_allowed(self, router, channel):
        rm = router.route_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_ORCHESTRATOR,
            source_id="orch-1",
            target_type=SOURCE_TYPE_WORKER,
            target_id="worker-1",
            message_kind="dispatch",
        )
        assert rm.source_type == "orchestrator"
        assert rm.target_type == "worker"


# ------------------------------------------------------------------
# Direct @agent dispatch
# ------------------------------------------------------------------


class TestDirectAgentDispatch:
    def test_explicit_at_agent_dispatch_user_to_worker(self, router, channel):
        rm = router.route_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_USER,
            source_id="user-1",
            target_type=TARGET_TYPE_AGENT,
            target_id="@builder",
            message_kind="dispatch",
            explicit_dispatch=True,
        )
        assert rm.source_type == "user"
        assert rm.target_type == "agent"
        assert rm.target_id == "@builder"
        assert rm.message_kind == "dispatch"

    def test_explicit_at_agent_dispatch_orchestrator_to_agent(
        self, router, channel
    ):
        rm = router.route_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_ORCHESTRATOR,
            source_id="orch-1",
            target_type=TARGET_TYPE_AGENT,
            target_id="@reviewer",
            message_kind="dispatch",
        )
        assert rm.target_type == "agent"
        assert rm.target_id == "@reviewer"

    def test_explicit_dispatch_to_system_target(self, router, channel):
        rm = router.route_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_USER,
            source_id="user-1",
            target_type=TARGET_TYPE_SYSTEM,
            target_id="@system",
            message_kind="system",
        )
        assert rm.target_type == "system"
        assert rm.target_id == "@system"


# ------------------------------------------------------------------
# route_event
# ------------------------------------------------------------------


class TestRouteEvent:
    def test_route_event_creates_event_record(self, router):
        ev = router.route_event(
            harness_run_id=None,
            event_type="status_change",
            event_source="harness-runner",
        )
        assert isinstance(ev, EventRecord)
        assert ev.event_type == "status_change"
        assert ev.event_source == "harness-runner"
        assert ev.event_id is not None
        assert ev.event_ts > 0

    def test_route_event_links_harness_run(self, router, harness_run):
        ev = router.route_event(
            harness_run_id=harness_run.harness_run_id,
            event_type="start",
            event_source="runner",
        )
        assert ev.harness_run_id == harness_run.harness_run_id

    def test_route_event_links_routed_message(self, router, channel):
        rm = router.route_default_user_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            user_id="user-1",
        )
        ev = router.route_event(
            harness_run_id=None,
            event_type="message_sent",
            event_source="router",
            routed_message_id=rm.routed_message_id,
            payload_ref="/path/to/payload.json",
        )
        assert ev.routed_message_id == rm.routed_message_id
        assert ev.event_payload_ref == "/path/to/payload.json"

    def test_route_event_rejects_null_event_type(self, router):
        with pytest.raises(ValueError, match="event_type"):
            router.route_event(
                harness_run_id=None,
                event_type="",
                event_source="src",
            )

    def test_route_event_rejects_null_event_source(self, router):
        with pytest.raises(ValueError, match="event_source"):
            router.route_event(
                harness_run_id=None,
                event_type="start",
                event_source="",
            )


# ------------------------------------------------------------------
# Query helpers
# ------------------------------------------------------------------


class TestQueries:
    def test_get_messages_by_channel(self, router, channel):
        router.route_default_user_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            user_id="user-1",
        )
        router.route_orchestrator_dispatch(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            orchestrator_id="orch-1",
            worker_id="worker-1",
        )
        msgs = router.get_messages_by_channel(channel.channel_id)
        assert len(msgs) == 2
        assert all(isinstance(m, RoutedMessage) for m in msgs)

    def test_get_messages_by_session(self, router, channel):
        router.route_default_user_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            user_id="user-1",
            session_id="session-A",
        )
        router.route_default_user_message(
            workspace_id=channel.workspace_id,
            channel_id=channel.channel_id,
            user_id="user-1",
            session_id="session-B",
        )
        msgs = router.get_messages_by_session("session-A")
        assert len(msgs) == 1
        assert msgs[0].session_id == "session-A"

    def test_get_events_by_harness_run(self, router, harness_run):
        router.route_event(
            harness_run_id=harness_run.harness_run_id,
            event_type="start",
            event_source="runner",
        )
        router.route_event(
            harness_run_id=harness_run.harness_run_id,
            event_type="end",
            event_source="runner",
        )
        events = router.get_events_by_harness_run(harness_run.harness_run_id)
        assert len(events) == 2
        assert all(isinstance(e, EventRecord) for e in events)


# ------------------------------------------------------------------
# Cross-cutting: every persisted message has source+target
# ------------------------------------------------------------------


class TestNoNullsInvariant:
    """Model rule §1: every persisted message/event has source+target."""

    def test_all_messages_have_source_and_target(self, router, channel):
        cases = [
            (SOURCE_TYPE_USER, "user-1", TARGET_TYPE_ORCHESTRATOR, "@orchestrator"),
            (SOURCE_TYPE_ORCHESTRATOR, "orch-1", SOURCE_TYPE_WORKER, "worker-1"),
            (SOURCE_TYPE_WORKER, "worker-1", TARGET_TYPE_ORCHESTRATOR, "@orchestrator"),
            (SOURCE_TYPE_USER, "user-1", TARGET_TYPE_ALL, "@all"),
            (SOURCE_TYPE_ORCHESTRATOR, "orch-1", TARGET_TYPE_AGENT, "@agent-a"),
            (SOURCE_TYPE_USER, "user-1", TARGET_TYPE_SYSTEM, "@system"),
        ]
        for src_type, src_id, tgt_type, tgt_id in cases:
            rm = router.route_message(
                workspace_id=channel.workspace_id,
                channel_id=channel.channel_id,
                source_type=src_type,
                source_id=src_id,
                target_type=tgt_type,
                target_id=tgt_id,
                message_kind="conversation",
            )
            assert rm.source_type, "source_type must be set"
            assert rm.source_id, "source_id must be set"
            assert rm.target_type, "target_type must be set"
            assert rm.target_id, "target_id must be set"

    def test_every_persisted_event_has_source(self, router, harness_run):
        ev = router.route_event(
            harness_run_id=harness_run.harness_run_id,
            event_type="status_change",
            event_source="harness-runner",
        )
        assert ev.event_source
        assert ev.event_type
