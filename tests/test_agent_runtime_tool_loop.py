"""Tests for the tool-aware loop in AgentRuntimeService.

We don't hit a real provider here — the openai_compatible code path is
monkey-patched to return canned responses.  The point is to exercise:

- the tools=[] parameter is added when effective tools exist
- tool_calls in the response are dispatched and the result is fed back
  as a role=tool message
- the loop terminates when the provider returns a plain text message
- the iteration cap holds even if the provider keeps calling tools
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.provider import Provider, ProviderRepository
from agent_workbench.models.routed_message import RoutedMessageRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.tool import ToolRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.agent_runtime_service import (
    AgentRuntimeService,
)
from agent_workbench.services.participant_service import ParticipantService
from agent_workbench.services.profile_service import ProfileService


def _seed_full_session(db):
    ws = WorkspaceRepository(db).create(tenant_id="t1", name="t")
    db.commit()
    ch = ChannelRepository(db).create(
        workspace_id=ws.workspace_id, channel_kind="chat", title="t",
    )
    db.commit()
    sess = SessionExtensionRepository(db).create(
        workspace_id=ws.workspace_id, session_type="work",
    )
    db.commit()
    # Link the session to the channel so AgentRuntimeService can find it.
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


def _make_profile_with_harness(db, provider, harness_type, *, with_harness_ref=True):
    profile = ProfileService(db).create_profile(
        name="tester",
        provider=provider.provider_id,
        model="stub-model",
        function="operator",
        harness=harness_type if with_harness_ref else None,
    )
    db.commit()
    return profile


def _add_agent_to_session(db, session_id, profile, user_id="user"):
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


class TestOpenAIToolLoop:
    def test_plain_reply_when_no_tools(self, db):
        """When the agent has no enabled tools, the request payload
        must NOT include a tools= field."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile = _make_profile_with_harness(db, provider, harness_type="shell")
        # Disable all shell tools so the negotiated list is empty.
        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)
        _add_agent_to_session(db, sid, profile)

        captured: List[Dict[str, Any]] = []

        def fake_urlopen(req, timeout):
            captured.append(json.loads(req.data.decode("utf-8")))
            return _FakeResp({
                "choices": [{
                    "message": {"role": "assistant", "content": "hi there"},
                }],
            })

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="hello", user_id="user",
            )

        # The provider saw exactly one request, with no "tools" key.
        assert len(captured) == 1
        assert "tools" not in captured[0]
        # The user message made it through.
        assert captured[0]["messages"][-1]["role"] == "user"
        assert captured[0]["messages"][-1]["content"] == "hello"
        # The reply was routed into the channel.
        rows = RoutedMessageRepository(db).list_by_session(sid)
        assert any("hi there" in (r.payload_ref or "") for r in rows)

    def test_tool_call_dispatched_and_result_returned(self, db, monkeypatch):
        """When the provider returns a tool_call, we dispatch it and
        feed the result back as a role=tool message."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile = _make_profile_with_harness(db, provider, harness_type="shell")
        import uuid
        ToolRepository(db).create(
            name=f"custom_run_{uuid.uuid4().hex[:6]}",
            harness_type="shell", adapter_method="start",
            description="run a command",
            input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            permission_class="write_local",
        )
        all_shell = ToolRepository(db).list_for_harness("shell")
        tool = next(t for t in all_shell if t.name.startswith("custom_run_"))
        tool_call_name = f"shell.{tool.name}"
        _add_agent_to_session(db, sid, profile)

        captured_requests: List[Dict[str, Any]] = []

        responses = [
            _FakeResp({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_xyz",
                            "type": "function",
                            "function": {
                                "name": tool_call_name,
                                "arguments": json.dumps({"command": "printf hello-from-tool"}),
                            },
                        }],
                    },
                }],
            }),
            _FakeResp({
                "choices": [{
                    "message": {"role": "assistant", "content": "tool said hi"},
                }],
            }),
        ]

        def fake_urlopen(req, timeout):
            captured_requests.append(json.loads(req.data.decode("utf-8")))
            return responses[len(captured_requests) - 1]

        monkeypatch.setenv("WORKBENCH_TOOL_COLLECT_TIMEOUT", "0.1")

        # Mock the shell adapter's start() to avoid real subprocess calls.
        from agent_workbench.adapters.shell import ShellAdapter
        from agent_workbench.models.harness_run import HarnessRunRepository

        def fake_start(adapter, *, workspace_id, session_id, command, **kw):
            hr = HarnessRunRepository(db).create(
                workspace_id=workspace_id,
                session_id=session_id,
                harness_type="shell",
                status="completed",
                control_capabilities=adapter.capabilities_dict(),
            )
            # ShellAdapter uses _processes, not _sessions
            adapter._processes[hr.harness_run_id] = {
                "process": object(),
                "session_id": session_id,
                "process_id": "1234",
                "stdout": "hello-from-tool\n",
                "stderr": "",
                "command": command,
                "pgid": None,
            }
            return hr.harness_run_id

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch.object(ShellAdapter, "start", new=fake_start):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="use the tool", user_id="user",
            )

        assert len(captured_requests) == 2
        first = captured_requests[0]
        assert "tools" in first
        assert len(first["tools"]) >= 1
        names = {t["function"]["name"] for t in first["tools"]}
        assert tool_call_name in names
        second = captured_requests[1]
        # The first assistant message + the tool result should both be
        # in the second request.
        last_messages = second["messages"][-2:]
        assert last_messages[0]["role"] == "assistant"
        assert last_messages[0].get("tool_calls")
        assert last_messages[1]["role"] == "tool"
        assert last_messages[1]["tool_call_id"] == "call_xyz"
        # The tool result content is JSON; we just assert it's non-empty.
        assert last_messages[1]["content"]

        # The final reply was routed to the channel.
        rows = RoutedMessageRepository(db).list_by_session(sid)
        assert any("tool said hi" in (r.payload_ref or "") for r in rows)

    def test_iteration_cap_holds(self, db, monkeypatch):
        """If the provider keeps emitting tool_calls, we stop after
        MAX_TOOL_ITERATIONS and surface a precise marker."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile = _make_profile_with_harness(db, provider, harness_type="shell")
        import uuid
        ToolRepository(db).create(
            name=f"loop_tool_{uuid.uuid4().hex[:6]}",
            harness_type="shell", adapter_method="start",
            description="loop",
            input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            permission_class="write_local",
        )
        all_shell = ToolRepository(db).list_for_harness("shell")
        tool = next(t for t in all_shell if t.name.startswith("loop_tool_"))
        tool_call_name = f"shell.{tool.name}"
        _add_agent_to_session(db, sid, profile)

        # Every response keeps requesting a new tool call.
        def make_loop_response():
            return _FakeResp({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_loop",
                            "type": "function",
                            "function": {
                                "name": tool_call_name,
                                "arguments": json.dumps({"command": "true"}),
                            },
                        }],
                    },
                }],
            })

        call_count = 0

        def fake_urlopen(req, timeout):
            nonlocal call_count
            call_count += 1
            return make_loop_response()

        monkeypatch.setenv("WORKBENCH_TOOL_COLLECT_TIMEOUT", "0.05")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="loop forever", user_id="user",
            )

        # The runtime caps the loop at the session's max_tool_iterations.
        # The session was created with type "work", so default is 25.
        from agent_workbench.models.session_extension import SessionExtensionRepository
        sess = SessionExtensionRepository(db).get_by_id(sid)
        expected_max = sess.max_tool_iterations or 5
        assert call_count == expected_max
        # The final routed message contains a clear "stopped" marker.
        rows = RoutedMessageRepository(db).list_by_session(sid)
        text_payloads = [
            r.payload_ref for r in rows
            if r.payload_ref and "agent_reply" in r.payload_ref
        ]
        assert text_payloads
        assert any("stopped after" in p for p in text_payloads)

    def test_no_tools_when_harness_has_no_catalog(self, db):
        """If the agent's harness has no enabled tools, the runtime
        must NOT add a tools= field to the request, even if the harness
        is set on the profile."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        # Create a profile with a harness that has no tools.
        profile = _make_profile_with_harness(db, provider, harness_type="hermes")
        # Disable all hermes tools so the negotiated list is empty.
        for t in ToolRepository(db).list_for_harness("hermes"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)
        _add_agent_to_session(db, sid, profile)

        captured: List[Dict[str, Any]] = []

        def fake_urlopen(req, timeout):
            captured.append(json.loads(req.data.decode("utf-8")))
            return _FakeResp({
                "choices": [{
                    "message": {"role": "assistant", "content": "no tools here"},
                }],
            })

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="hello", user_id="user",
            )

        assert len(captured) == 1
        assert "tools" not in captured[0]


# ── Auto-turn chain tests ────────────────────────────────────────────────


def _seed_session_with_auto_turns(db, max_auto_turns=5):
    """Seed a session with 2 agents (Alpha, Beta) and auto-turns enabled.

    Agents are added in order Beta, Alpha so that Alpha (who contains the
    @mention) is the LAST participant in the details list, meaning the
    auto-turn loop checks Alpha's reply for @mentions.
    """
    ws = WorkspaceRepository(db).create(tenant_id="t1", name="t")
    db.commit()
    ch = ChannelRepository(db).create(
        workspace_id=ws.workspace_id, channel_kind="chat", title="t",
    )
    db.commit()
    sess = SessionExtensionRepository(db).create(
        workspace_id=ws.workspace_id, session_type="work",
        max_auto_turns=max_auto_turns,
    )
    db.commit()
    ChannelRepository(db).update_active_session(
        ch.channel_id, active_session_id=sess.session_id,
    )
    db.commit()

    provider = ProviderRepository(db).create(
        name="p", provider_kind="openai_compatible", endpoint_url="http://stub/v1",
        api_key_env_var=None, default_model="stub-model",
    )
    profile_a = ProfileService(db).create_profile(
        name="Alpha", provider=provider.provider_id, model="stub-model",
        function="operator", harness="shell",
    )
    profile_b = ProfileService(db).create_profile(
        name="Beta", provider=provider.provider_id, model="stub-model",
        function="operator", harness="shell",
    )
    db.commit()

    # Add Beta first, Alpha second so Alpha is last in the details list
    ParticipantService(db).add_participant(
        session_id=sess.session_id, agent_profile_id=profile_b.agent_profile_id,
        participant_role="member", added_by="user",
    )
    ParticipantService(db).add_participant(
        session_id=sess.session_id, agent_profile_id=profile_a.agent_profile_id,
        participant_role="member", added_by="user",
    )
    return ws.workspace_id, ch.channel_id, sess.session_id


class TestAutoTurnChain:
    """Tests for the iterative agent auto-turn feature (max_auto_turns > 0)."""

    def test_auto_turn_chain_continues_on_at_mention(self, db):
        """When max_auto_turns > 0 and an agent's reply contains @mention
        of another participant, the mentioned agent is auto-dispatched."""
        ws, ch, sid = _seed_session_with_auto_turns(db, max_auto_turns=5)
        responses = [
            _FakeResp({"choices": [{"message": {"role": "assistant", "content": "Sure, I can help"}}]}),
            _FakeResp({"choices": [{"message": {"role": "assistant", "content": "Let me check with @Beta"}}]}),
            _FakeResp({"choices": [{"message": {"role": "assistant", "content": "Here's what I found"}}]}),
        ]
        call_count = 0

        def fake_urlopen(req, timeout):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="hello", user_id="user",
            )

        # 3 calls: Beta (initial), Alpha (initial), Beta (auto-turn)
        assert call_count == 3
        # All 3 replies should be routed as conversation messages
        rows = RoutedMessageRepository(db).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) == 3
        # The last message is the auto-triggered Beta reply
        last_payload = conv_msgs[-1].payload_ref or ""
        assert "Here's what I found" in last_payload
        assert "Beta" in last_payload

    def test_no_auto_turn_when_max_auto_turns_is_zero(self, db):
        """Default max_auto_turns=0 means no auto-turn chain runs,
        even if an agent's reply contains @mention."""
        ws, ch, sid = _seed_session_with_auto_turns(db, max_auto_turns=0)
        responses = [
            _FakeResp({"choices": [{"message": {"role": "assistant", "content": "Sure, I can help"}}]}),
            _FakeResp({"choices": [{"message": {"role": "assistant", "content": "Check with @Beta"}}]}),
        ]
        call_count = 0

        def fake_urlopen(req, timeout):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="hi", user_id="user",
            )

        # Only 2 calls: both agents respond, no auto-turn
        assert call_count == 2
        rows = RoutedMessageRepository(db).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) == 2

    def test_auto_turn_respects_max_auto_turns_limit(self, db):
        """The chain stops after max_auto_turns auto-turns, even if
        @mentions keep appearing."""
        ws, ch, sid = _seed_session_with_auto_turns(db, max_auto_turns=2)
        call_count = 0

        def fake_urlopen(req, timeout):
            nonlocal call_count
            call_count += 1
            # Beta (first participant) says "Ask @Alpha"
            # Alpha (second participant) says "Ask @Beta"
            # → auto-turn chain bounces between them
            if call_count % 2 == 1:  # odd calls: Beta's turn
                return _FakeResp({"choices": [{"message": {"role": "assistant", "content": "Ask @Alpha"}}]})
            else:  # even calls: Alpha's turn
                return _FakeResp({"choices": [{"message": {"role": "assistant", "content": "Ask @Beta"}}]})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="start", user_id="user",
            )

        # 4 calls: 2 initial + 2 auto-turns (max_auto_turns=2)
        rows = RoutedMessageRepository(db).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        # 2 initial + 2 auto-turns = 4 messages
        assert len(conv_msgs) == 4, f"Expected 4 messages, got {len(conv_msgs)}"

    def test_auto_turn_self_loop_guard(self, db):
        """An agent mentioning itself does NOT trigger an auto-turn."""
        ws, ch, sid = _seed_session_with_auto_turns(db, max_auto_turns=5)
        responses = [
            # Beta (added first) replies
            _FakeResp({"choices": [{"message": {"role": "assistant", "content": "I'm Beta, @Beta here"}}]}),
            # Alpha (added second) replies
            _FakeResp({"choices": [{"message": {"role": "assistant", "content": "I'm Alpha, @Alpha says hi"}}]}),
        ]
        call_count = 0

        def fake_urlopen(req, timeout):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="hi", user_id="user",
            )

        # Only 2 calls — self-mentions are not followed
        assert call_count == 2

    def test_auto_turn_skipped_when_target_agent_name_set(self, db):
        """When target_agent_name is set (selective dispatch), the
        auto-turn chain is not entered."""
        ws, ch, sid = _seed_session_with_auto_turns(db, max_auto_turns=5)
        responses = [
            _FakeResp({"choices": [{"message": {"role": "assistant", "content": "Check with @Beta"}}]}),
        ]
        call_count = 0

        # Disable all shell tools so no tool calls interfere
        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)

        def fake_urlopen(req, timeout):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="hey @Alpha", user_id="user",
                target_agent_name="Alpha",
            )

        # Only 1 call: selective dispatch to Alpha, no auto-turn
        assert call_count == 1

    def test_auto_turn_chain_stops_when_target_not_found(self, db):
        """If the @mention targets an agent not in the session, the
        chain ends without error."""
        ws, ch, sid = _seed_session_with_auto_turns(db, max_auto_turns=5)
        # Disable all shell tools
        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)

        responses = [
            _FakeResp({"choices": [{"message": {"role": "assistant", "content": "Sure, I can help"}}]}),
            _FakeResp({"choices": [{"message": {"role": "assistant", "content": "Let me ask @Nonexistent"}}]}),
        ]
        call_count = 0

        def fake_urlopen(req, timeout):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            AgentRuntimeService(db).generate_for_session(
                session_id=sid, user_body="go", user_id="user",
            )

        # 2 calls: both agents respond, auto-turn finds no target → chain ends
        assert call_count == 2


class TestSeedBuiltinTools:
    def test_builtin_seeded_idempotently(self, tmp_path):
        """Use a fresh DB to verify the seeder from scratch.

        The conftest's ``db`` fixture is shared with other tests, so
        we open a brand-new DB here and exercise ``seed_builtin_tools``
        against a clean slate.
        """
        from agent_workbench.db import get_connection, apply_migrations
        from agent_workbench.services.tool_seeds import seed_builtin_tools
        from agent_workbench.models.tool import ToolRepository

        db = get_connection(str(tmp_path / "seed.db"))
        try:
            apply_migrations(db)
            n1 = seed_builtin_tools(db)
            n2 = seed_builtin_tools(db)
            assert n1 == 4  # the 4 builtin tools
            assert n2 == 0  # second call is a no-op
            names = {(t.harness_type, t.name) for t in ToolRepository(db).list_enabled()}
            assert ("shell", "run_command") in names
            assert ("hermes", "run_command") in names
            assert ("hermes", "write_file") in names
            assert ("hermes", "delegate_subagent") in names
        finally:
            db.close()

    def test_builtin_seed_refreshes_hermes_tool_contract(self, tmp_path):
        """Existing builtin rows must be refreshed when the contract changes.

        This guards the real regression: a stale DB row kept telling the
        model that ``harness_run_id`` was mandatory even though the
        dispatcher now auto-spawns a Hermes session when it is omitted.
        """
        from agent_workbench.db import get_connection, apply_migrations
        from agent_workbench.services.tool_seeds import seed_builtin_tools
        from agent_workbench.models.tool import ToolRepository

        db = get_connection(str(tmp_path / "seed-refresh.db"))
        try:
            apply_migrations(db)
            seed_builtin_tools(db)
            repo = ToolRepository(db)

            hermes_run = next(
                t for t in repo.list_for_harness("hermes") if t.name == "run_command"
            )
            stale_schema = {
                "type": "object",
                "properties": {
                    "harness_run_id": {
                        "type": "string",
                        "description": "ID of an existing hermes HarnessRun.",
                    },
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                },
                "required": ["harness_run_id", "command"],
            }
            repo.update(
                hermes_run.tool_id,
                description=(
                    "Run a shell command inside a running hermes session. "
                    "You must already have a hermes harness run to attach to; "
                    "pass its ``harness_run_id``."
                ),
                input_schema=stale_schema,
            )

            refreshed = seed_builtin_tools(db)
            assert refreshed == 0

            hermes_run = repo.get_by_id(hermes_run.tool_id)
            assert hermes_run is not None
            assert "auto-spawns" in hermes_run.description
            assert hermes_run.input_schema_json["required"] == ["command"]
            assert (
                hermes_run.input_schema_json["properties"]["harness_run_id"]["description"]
                == "Optional ID of an existing Hermes HarnessRun. If omitted, Agent Workbench auto-spawns one."
            )
        finally:
            db.close()
