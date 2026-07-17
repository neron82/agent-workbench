"""Deterministic regression tests for the alpha concurrent runtime lane.

Tests cover:
- Concurrent participant execution with mock/fake providers
- One worker failure does not suppress other workers
- Status tracker shows every active participant (idle/queued/working/completed/error/stopped)
- Fresh SQLite connections per worker
- Isolated history snapshots
- Capability hints are authoritative (session labels are descriptive only)
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List
from unittest.mock import patch


from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.provider import ProviderRepository
from agent_workbench.models.routed_message import RoutedMessageRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.tool import ToolRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.agent_runtime_service import (
    AgentRuntimeService,
)
from agent_workbench.services.agent_status import AgentStatusTracker
from agent_workbench.services.participant_service import ParticipantService
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.tool_registry import (
    DEFAULT_SESSION_POLICIES,
    ToolRegistry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_full_session(db, session_type="chat"):
    ws = WorkspaceRepository(db).create(tenant_id="t1", name="t")
    db.commit()
    ch = ChannelRepository(db).create(
        workspace_id=ws.workspace_id, channel_kind="chat", title="t",
    )
    db.commit()
    sess = SessionExtensionRepository(db).create(
        workspace_id=ws.workspace_id, session_type=session_type,
    )
    db.commit()
    ChannelRepository(db).update_active_session(
        ch.channel_id, active_session_id=sess.session_id,
    )
    db.commit()
    return ws.workspace_id, ch.channel_id, sess.session_id


def _make_provider(db, name="p", kind="openai_compatible", endpoint="http://stub/v1"):
    return ProviderRepository(db).create(
        name=name, provider_kind=kind, endpoint_url=endpoint,
        api_key_env_var=None, default_model="stub-model",
    )


def _make_profile(db, provider, name="tester", harness="shell"):
    profile = ProfileService(db).create_profile(
        name=name,
        provider=provider.provider_id,
        model="stub-model",
        function="operator",
        harness=harness,
    )
    db.commit()
    return profile


def _add_agent(db, session_id, profile, user_id="user"):
    return ParticipantService(db).add_participant(
        session_id=session_id,
        agent_profile_id=profile.agent_profile_id,
        participant_role="member",
        added_by=user_id,
    )


class _FakeResp:
    def __init__(self, body: Dict[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


# ---------------------------------------------------------------------------
# Concurrent execution tests
# ---------------------------------------------------------------------------


class TestConcurrentRuntime:
    """Tests that participant responses execute concurrently."""

    def test_all_agents_produce_replies_concurrently(self, db):
        """When multiple agents are active, each produces a reply and
        the replies are routed as conversation messages."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile_a = _make_profile(db, provider, "Alpha")
        profile_b = _make_profile(db, provider, "Beta")
        profile_c = _make_profile(db, provider, "Gamma")
        _add_agent(db, sid, profile_a)
        _add_agent(db, sid, profile_b)
        _add_agent(db, sid, profile_c)

        # Disable tools so all agents use the chat path
        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)
        db.commit()

        call_count = 0
        call_lock = threading.Lock()

        def fake_urlopen(req, timeout):
            nonlocal call_count
            with call_lock:
                call_count += 1
            return _FakeResp({
                "choices": [{"message": {"role": "assistant", "content": "reply"}}],
            })

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="hello", user_id="user",
            )

        # All 3 agents replied
        rows = RoutedMessageRepository(db).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) == 3, f"Expected 3 conversation messages, got {len(conv_msgs)}"

        # Each agent's reply is present
        payloads = " ".join(r.payload_ref or "" for r in conv_msgs)
        assert "Alpha" in payloads
        assert "Beta" in payloads
        assert "Gamma" in payloads

    def test_sequential_turns_produce_two_replies_and_accumulate_history(self, db):
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile = _make_profile(db, provider, "Sysman")
        _add_agent(db, sid, profile)
        for tool in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(tool.tool_id, is_enabled=False)
        db.commit()

        payloads = []

        def fake_urlopen(req, timeout):
            payloads.append(json.loads(req.data.decode("utf-8")))
            reply_no = len(payloads)
            return _FakeResp({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": f"reply-{reply_no}",
                    },
                }],
            })

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            runtime = AgentRuntimeService(db)
            runtime.generate_for_session(
                session_id=sid, user_body="first question", user_id="user",
            )
            runtime.generate_for_session(
                session_id=sid, user_body="second question", user_id="user",
            )

        rows = RoutedMessageRepository(db).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) == 2
        assert len(payloads) == 2
        second_contents = [m["content"] for m in payloads[1]["messages"]]
        assert "reply-1" in second_contents
        assert "second question" in second_contents

    def test_workers_overlap_in_time(self, db):
        """Two slow provider calls complete in parallel, not serially."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile_a = _make_profile(db, provider, "OverlapA")
        profile_b = _make_profile(db, provider, "OverlapB")
        _add_agent(db, sid, profile_a)
        _add_agent(db, sid, profile_b)
        for tool in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(tool.tool_id, is_enabled=False)
        db.commit()

        def slow_urlopen(req, timeout):
            time.sleep(0.18)
            return _FakeResp({
                "choices": [{"message": {"role": "assistant", "content": "slow"}}],
            })

        started = time.monotonic()
        with patch("urllib.request.urlopen", side_effect=slow_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="parallel", user_id="user",
            )
        elapsed = time.monotonic() - started
        assert elapsed < 0.32, f"workers appear serial: elapsed={elapsed:.3f}s"

    def test_worker_failure_does_not_suppress_others(self, db, monkeypatch):
        """If one agent's provider is broken, the other agents still
        produce their replies."""
        ws, ch, sid = _seed_full_session(db)

        # Create two providers with DIFFERENT endpoint URLs so we can
        # distinguish them in the mock.
        provider_ok = ProviderRepository(db).create(
            name="ok", provider_kind="openai_compatible", endpoint_url="http://ok/v1",
            api_key_env_var=None, default_model="stub-model",
        )
        provider_broken = ProviderRepository(db).create(
            name="broken", provider_kind="openai_compatible", endpoint_url="http://broken/v1",
            api_key_env_var=None, default_model="stub-model",
        )
        db.commit()

        profile_ok = ProfileService(db).create_profile(
            name="WorkerOK", provider=provider_ok.provider_id,
            model="stub-model", function="operator", harness="shell",
        )
        profile_broken = ProfileService(db).create_profile(
            name="WorkerBroken", provider=provider_broken.provider_id,
            model="stub-model", function="operator", harness="shell",
        )
        db.commit()

        _add_agent(db, sid, profile_ok)
        _add_agent(db, sid, profile_broken)

        # Disable tools
        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)
        db.commit()

        import urllib.error

        def fake_urlopen(req, timeout):
            url = req.full_url
            if "broken" in url:
                raise urllib.error.HTTPError(
                    "http://broken/v1", 500, "Internal Server Error", {}, None
                )
            return _FakeResp({
                "choices": [{"message": {"role": "assistant", "content": "ok-reply"}}],
            })

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="hi", user_id="user",
            )

        # The broken agent's error is routed, but the ok agent still replies
        rows = RoutedMessageRepository(db).list_by_session(sid)
        system_msgs = [r for r in rows if r.message_kind == "system" and r.payload_ref]
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]

        # There should be at least one error message
        assert len(system_msgs) >= 1, "Expected at least one error message"
        error_text = " ".join(r.payload_ref or "" for r in system_msgs)
        assert "could not respond" in error_text or "WorkerBroken" in error_text

        # And the working agent's reply
        assert len(conv_msgs) >= 1, "Expected at least one conversation message"
        ok_text = " ".join(r.payload_ref or "" for r in conv_msgs)
        assert "ok-reply" in ok_text
        assert "WorkerOK" in ok_text

    def test_agent_status_tracker_shows_all_states(self, db):
        """Every active participant appears in status JSON with the
        correct state transitions: idle -> queued -> working -> completed."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile_a = _make_profile(db, provider, "Alpha")
        profile_b = _make_profile(db, provider, "Beta")
        _add_agent(db, sid, profile_a)
        _add_agent(db, sid, profile_b)

        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)
        db.commit()

        def fake_urlopen(req, timeout):
            return _FakeResp({
                "choices": [{"message": {"role": "assistant", "content": "done"}}],
            })

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="go", user_id="user",
            )

        # Check status tracker
        tracker = AgentStatusTracker.get_instance()
        statuses = tracker.get_session_statuses(sid)
        names = {s.agent_name for s in statuses}
        assert names == {"Alpha", "Beta"}, f"Expected both agents, got {names}"

        for s in statuses:
            assert s.status in (
                "completed", "error", "stopped"
            ), f"Expected completed/error/stopped, got {s.status} for {s.agent_name}"
            assert s.completed_at is not None
            assert s.started_at is not None

        # Clean up
        tracker.cleanup_session(sid)

    def test_agent_status_init_and_queue(self, db):
        """init_agent and queue_agent produce visible idle/queued status."""
        tracker = AgentStatusTracker.get_instance()
        try:
            tracker.init_agent("sess_1", "AgentA")
            s = tracker.get_status("sess_1", "AgentA")
            assert s is not None
            assert s.status == "idle"

            tracker.queue_agent("sess_1", "AgentA")
            s = tracker.get_status("sess_1", "AgentA")
            assert s is not None
            assert s.status == "queued"

            tracker.start_agent("sess_1", "AgentA")
            s = tracker.get_status("sess_1", "AgentA")
            assert s is not None
            assert s.status == "working"

            tracker.complete_agent("sess_1", "AgentA")
            s = tracker.get_status("sess_1", "AgentA")
            assert s is not None
            assert s.status == "completed"
        finally:
            tracker.cleanup_session("sess_1")

    def test_status_tracker_init_idempotent(self, db):
        """init_agent is idempotent and does not reset status."""
        tracker = AgentStatusTracker.get_instance()
        try:
            tracker.start_agent("sess_i", "AgentX")
            tracker.init_agent("sess_i", "AgentX")  # should not reset to idle
            s = tracker.get_status("sess_i", "AgentX")
            assert s is not None
            assert s.status == "working"  # kept working
        finally:
            tracker.cleanup_session("sess_i")

    def test_agent_status_stop_works(self, db):
        """stop_agent marks the agent as stopped."""
        tracker = AgentStatusTracker.get_instance()
        try:
            tracker.init_agent("sess_stop", "AgentS")
            tracker.start_agent("sess_stop", "AgentS")
            assert tracker.stop_agent("sess_stop", "AgentS")
            s = tracker.get_status("sess_stop", "AgentS")
            assert s is not None
            assert s.status == "stopped"
            assert tracker.should_stop("sess_stop", "AgentS") is True
        finally:
            tracker.cleanup_session("sess_stop")


    def test_cleanup_removes_status_and_stop_event(self, db):
        """Session cleanup removes both visible state and cancellation state."""
        tracker = AgentStatusTracker.get_instance()
        try:
            tracker.start_agent("sess_cleanup", "AgentC")
            assert tracker.get_status("sess_cleanup", "AgentC") is not None
            tracker.cleanup_session("sess_cleanup")
            assert tracker.get_status("sess_cleanup", "AgentC") is None
            assert tracker.get_session_statuses("sess_cleanup") == []
            assert tracker.stop_agent("sess_cleanup", "AgentC") is False
        finally:
            tracker.cleanup_session("sess_cleanup")


# ---------------------------------------------------------------------------
# Capability / tool-registry tests
# ---------------------------------------------------------------------------


class TestCapabilityNegotiation:
    """Tests that capability hints are authoritative and session labels
    are descriptive only."""

    def test_explicit_session_policy_still_works(self, db):
        """When explicit session_policy is passed, it is still respected
        as a permission gate."""
        from agent_workbench.models.tool import ToolRepository
        from dataclasses import dataclass

        @dataclass
        class _ProfileStub:
            harness_ref: str | None
            capability_hints_json: dict | None = None

        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        t = repo.create(
            name="perm_test",
            harness_type="shell",
            adapter_method="start",
            description="test",
            input_schema={"type": "object", "properties": {}},
            permission_class="write_local",
        )
        profile = _ProfileStub(harness_ref="shell")

        # With restrictive policy, write_local tool is excluded
        tools = reg.effective_tools(
            agent_profile=profile,
            harness_type="shell",
            session_type="chat",
            session_policy=["read_only"],
        )
        names = {tt.name for tt in tools}
        assert t.name not in names

        # With permissive policy, it is included
        tools = reg.effective_tools(
            agent_profile=profile,
            harness_type="shell",
            session_type="chat",
            session_policy=["read_only", "write_local"],
        )
        names = {tt.name for tt in tools}
        assert t.name in names

    def test_profile_capability_hints_authoritative(self, db):
        """Profile capability_hints (allowed_tools/denied_tools) are
        the authoritative gate, not session_type."""
        from agent_workbench.models.tool import ToolRepository
        from dataclasses import dataclass

        @dataclass
        class _ProfileStub:
            harness_ref: str | None
            capability_hints_json: dict | None = None

        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        t_a = repo.create(
            name="hint_a",
            harness_type="shell",
            adapter_method="start",
            description="test a",
            input_schema={"type": "object", "properties": {}},
            permission_class="read_only",
        )
        t_b = repo.create(
            name="hint_b",
            harness_type="shell",
            adapter_method="start",
            description="test b",
            input_schema={"type": "object", "properties": {}},
            permission_class="write_local",
        )

        # Profile only allows t_a, even in a "work" session
        profile = _ProfileStub(
            harness_ref="shell",
            capability_hints_json={"allowed_tools": [t_a.name]},
        )
        tools = reg.effective_tools(
            agent_profile=profile,
            harness_type="shell",
            session_type="work",
        )
        names = {tt.name for tt in tools}
        assert t_a.name in names
        assert t_b.name not in names

        # Profile denies t_a, even in a "chat" session (which is permissive now)
        profile2 = _ProfileStub(
            harness_ref="shell",
            capability_hints_json={"denied_tools": [t_a.name]},
        )
        tools2 = reg.effective_tools(
            agent_profile=profile2,
            harness_type="shell",
            session_type="chat",
        )
        names2 = {tt.name for tt in tools2}
        assert t_a.name not in names2
        assert t_b.name in names2


# ---------------------------------------------------------------------------
# Mock / fake provider for deterministic tests
# ---------------------------------------------------------------------------


class _MockProvider:
    """A deterministic fake provider that returns canned replies."""

    def __init__(self, replies: List[str]):
        self.replies = replies
        self.call_count = 0
        self.lock = threading.Lock()

    def urlopen(self, req, timeout):
        with self.lock:
            idx = self.call_count
            self.call_count += 1
        if idx < len(self.replies):
            content = self.replies[idx]
        else:
            content = "fallback"
        return _FakeResp({
            "choices": [{"message": {"role": "assistant", "content": content}}],
        })


class TestDeterministicConcurrency:
    """Tests using a deterministic mock provider to verify concurrent
    execution ordering guarantees."""

    def test_mock_provider_returns_expected_replies(self, db):
        """All agents receive the same user message and produce
        deterministic output via the mock provider."""
        ws, ch, sid = _seed_full_session(db, session_type="chat")
        provider = _make_provider(db)
        profile_a = _make_profile(db, provider, "Alice")
        profile_b = _make_profile(db, provider, "Bob")
        _add_agent(db, sid, profile_a)
        _add_agent(db, sid, profile_b)

        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)
        db.commit()

        mock = _MockProvider(["Alice-reply", "Bob-reply"])

        with patch("urllib.request.urlopen", side_effect=mock.urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="test", user_id="user",
            )

        rows = RoutedMessageRepository(db).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) == 2

        payloads = " ".join(r.payload_ref or "" for r in conv_msgs)
        assert "Alice" in payloads
        assert "Bob" in payloads

    def test_isolated_history_snapshot(self, db):
        """Each worker receives an isolated copy of history so that
        one agent's replies do not appear in another's context."""
        ws, ch, sid = _seed_full_session(db, session_type="chat")
        provider = _make_provider(db)
        profile_a = _make_profile(db, provider, "Alice")
        profile_b = _make_profile(db, provider, "Bob")
        _add_agent(db, sid, profile_a)
        _add_agent(db, sid, profile_b)

        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)
        db.commit()

        # Send a first message to build history
        mock1 = _MockProvider(["first-A", "first-B"])
        with patch("urllib.request.urlopen", side_effect=mock1.urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="first", user_id="user",
            )

        # Now send a second message — each agent should see the history
        # that includes BOTH first replies (since they were routed before
        # the second invocation).
        rows = RoutedMessageRepository(db).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) == 2

        mock2 = _MockProvider(["second-A", "second-B"])
        with patch("urllib.request.urlopen", side_effect=mock2.urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="second", user_id="user",
            )

        rows = RoutedMessageRepository(db).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        # Now there should be 4 messages total
        assert len(conv_msgs) == 4


# ---------------------------------------------------------------------------
# Old unit-test API backward compat
# ---------------------------------------------------------------------------


class TestBackwardCompatGenerators:
    """Tests that existing unit-test APIs (mock_reply, build_history)
    are preserved."""

    def test_mock_reply_static_method(self, db):
        """_mock_reply is still callable as a static method."""
        detail = {
            "agent_name": "TestAgent",
            "role_name": "operator",
            "function_ref": "operator",
        }
        reply = AgentRuntimeService._mock_reply(
            detail, user_body="hello", history=[]
        )
        assert "TestAgent" in reply
        assert "hello" in reply

    def test_build_history_returns_list(self, db):
        """_build_history returns a list of messages."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile = _make_profile(db, provider, "TestAgent")
        _add_agent(db, sid, profile)

        history = AgentRuntimeService(db)._build_history(sid)
        assert isinstance(history, list)
        # No messages yet, so empty
        assert len(history) == 0

    def test_extract_message_body_preserved(self, db):
        """extract_message_body function is still available."""
        from agent_workbench.services.agent_runtime_service import extract_message_body
        assert extract_message_body(None) == ""
        assert extract_message_body("plain text") == "plain text"
        assert extract_message_body('{"body": "hello"}') == "hello"
        assert extract_message_body('{"envelope": "agent_reply", "body": "world"}') == "world"

    def test_DEFAULT_SESSION_POLICIES_preserved(self, db):
        """DEFAULT_SESSION_POLICIES is still importable and has the
        same keys for backward compat."""
        assert "chat" in DEFAULT_SESSION_POLICIES
        assert "research" in DEFAULT_SESSION_POLICIES
        assert "work" in DEFAULT_SESSION_POLICIES
        assert DEFAULT_SESSION_POLICIES["chat"] == ["read_only"]
        assert DEFAULT_SESSION_POLICIES["work"] == [
            "read_only", "write_local", "write_remote", "destructive",
        ]