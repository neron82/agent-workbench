"""Regression test: verify successive messages in a session all trigger agent responses.

This reproduces the reported bug where only the first turn produces an agent
response and subsequent turns are silently dropped.
"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch


from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.provider import ProviderRepository
from agent_workbench.models.routed_message import RoutedMessageRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.tool import ToolRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.agent_runtime_service import (
    AgentRuntimeService,
    run_agent_responses,
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
        workspace_id=ws.workspace_id, session_type="chat",
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


def _make_profile(db, provider, name="sysman"):
    profile = ProfileService(db).create_profile(
        name=name,
        provider=provider.provider_id,
        model="stub-model",
        function="operator",
        harness="shell",
    )
    db.commit()
    return profile


def _add_agent(db, session_id, profile):
    return ParticipantService(db).add_participant(
        session_id=session_id,
        agent_profile_id=profile.agent_profile_id,
        participant_role="member",
        added_by="user",
    )


class _FakeResp:
    def __init__(self, body):
        self._body = json.dumps(body).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self._body


class TestMultiTurnBug:
    """Reproduce the multi-turn chat bug: successive messages get no agent response."""

    def test_three_sequential_turns_all_produce_replies(self, db):
        """Three sequential user messages should each trigger an agent reply."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile = _make_profile(db, provider, "sysman")
        _add_agent(db, sid, profile)
        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)
        db.commit()

        call_log = []

        def fake_urlopen(req, timeout):
            call_log.append(json.loads(req.data.decode("utf-8")))
            reply_no = len(call_log)
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
            # Turn 1
            runtime.generate_for_session(
                session_id=sid, user_body="first question", user_id="user",
            )
            # Turn 2
            runtime.generate_for_session(
                session_id=sid, user_body="second question", user_id="user",
            )
            # Turn 3
            runtime.generate_for_session(
                session_id=sid, user_body="third question", user_id="user",
            )

        rows = RoutedMessageRepository(db).list_by_session(sid)
        conv_msgs = [r for r in rows if r.message_kind == "conversation"]
        assert len(conv_msgs) == 3, (
            f"Expected 3 conversation messages (3 turns), got {len(conv_msgs)}"
        )
        assert len(call_log) == 3, (
            f"Expected 3 LLM calls (3 turns), got {len(call_log)}"
        )

    def test_async_multi_turn_all_produce_replies(self, db, tmp_path):
        """Async path: each user message triggers a daemon thread that produces a reply."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile = _make_profile(db, provider, "sysman")
        _add_agent(db, sid, profile)
        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)
        db.commit()

        call_log = []
        call_lock = threading.Lock()

        def fake_urlopen(req, timeout):
            with call_lock:
                call_log.append(json.loads(req.data.decode("utf-8")))
                reply_no = len(call_log)
            return _FakeResp({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": f"async-reply-{reply_no}",
                    },
                }],
            })

        db_path = str(tmp_path / "multi-turn-async.db")
        import sqlite3
        # Use SQLite's backup API; copying only the main WAL file omits
        # committed pages that are still held in the -wal sidecar.
        conn = sqlite3.connect(db_path)
        db.backup(conn)
        conn.close()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            # Turn 1
            run_agent_responses(
                db_path=db_path, session_id=sid,
                user_body="async first", user_id="user",
            )
            # Turn 2
            run_agent_responses(
                db_path=db_path, session_id=sid,
                user_body="async second", user_id="user",
            )

        from agent_workbench.db import get_connection
        conn2 = get_connection(db_path)
        try:
            rows = RoutedMessageRepository(conn2).list_by_session(sid)
            conv_msgs = [r for r in rows if r.message_kind == "conversation"]
            assert len(conv_msgs) == 2, (
                f"Expected 2 conversation messages, got {len(conv_msgs)}"
            )
        finally:
            conn2.close()

    def test_build_history_includes_all_user_messages(self, db):
        """_build_history should include ALL user messages, not just the first."""
        ws, ch, sid = _seed_full_session(db)
        provider = _make_provider(db)
        profile = _make_profile(db, provider, "sysman")
        _add_agent(db, sid, profile)
        for t in ToolRepository(db).list_for_harness("shell"):
            ToolRepository(db).update(t.tool_id, is_enabled=False)
        db.commit()

        # Insert two user messages directly
        from agent_workbench.services.routing_service import RoutingService
        routing = RoutingService(db)
        routing.route_message(
            workspace_id=ws, channel_id=ch,
            source_type="user", source_id="user",
            target_type="orchestrator", target_id="@orchestrator",
            message_kind="conversation",
            session_id=sid,
            payload_ref=json.dumps({"body": "first msg", "from": "user"}),
        )
        routing.route_message(
            workspace_id=ws, channel_id=ch,
            source_type="user", source_id="user",
            target_type="orchestrator", target_id="@orchestrator",
            message_kind="conversation",
            session_id=sid,
            payload_ref=json.dumps({"body": "second msg", "from": "user"}),
        )

        runtime = AgentRuntimeService(db)
        history = runtime._build_history(sid)
        contents = [h["content"] for h in history]
        assert "first msg" in contents, f"History missing 'first msg': {contents}"
        assert "second msg" in contents, f"History missing 'second msg': {contents}"
        assert len(history) >= 2, f"Expected >=2 history entries, got {len(history)}"
