"""Tests for the Server-Sent Events chat stream and the bubble UI.

These tests cover three layers:

1. Bubble-helper unit tests — pure-Python logic, no Flask required.
2. SSE route tests — generator, mimetype, Accept-header fallback, the
   ``id`` / ``event`` / ``data`` frame format, and the heartbeat.
3. Polling fallback test — confirms the redirect from
   ``/messages/stream/<id>`` to ``/messages/list/<id>/since`` works
   when ``Accept`` does not contain ``text/event-stream``.

The bubble render and the polling-since response are also covered to
guard against regressions in the new HTML structure (which other
templates / panels might eventually depend on).
"""

from __future__ import annotations

import json
from typing import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.routing_service import RoutingService
from agent_workbench.web import create_app
from agent_workbench.web.bubble_helpers import (
    bubble_display_name,
    bubble_initials,
    bubble_role,
    bubble_time,
)
from agent_workbench.web.messages import visible_messages_for_session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app_db_path(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("messages-sse") / "workbench.db"
    conn = get_connection(str(path))
    apply_migrations(conn)
    conn.close()
    return str(path)


@pytest.fixture()
def workspace_id(app_db_path: str) -> str:
    conn = get_connection(app_db_path)
    try:
        repo = WorkspaceRepository(conn)
        ws = repo.create(tenant_id="sse", name="SSE WS")
        return ws.workspace_id
    finally:
        conn.close()


@pytest.fixture()
def app(app_db_path: str) -> Iterator[Flask]:
    application = create_app(db_path=app_db_path)
    # Speed up the heartbeat for the test that pokes at it.
    application.config["WORKBENCH_SSE_HEARTBEAT"] = 0.3
    application.config["WORKBENCH_SSE_POLL"] = 0.1
    application.config.update(TESTING=True)
    yield application


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def _create_session_with_channel(
    client: FlaskClient, workspace_id: str, title: str = "sse"
) -> str:
    """Helper: create a chat channel with a starter session, return session_id."""
    create = client.post(
        "/channels",
        data={
            "workspace_id": workspace_id,
            "channel_kind": "chat",
            "title": title,
            "create_session": "1",
        },
        follow_redirects=False,
    )
    assert create.status_code == 302
    channel_id = create.headers["Location"].rsplit("/", 1)[-1]
    from agent_workbench.db import get_connection
    from agent_workbench.models.channel import ChannelRepository

    conn = get_connection(client.application.config["WORKBENCH_DB_PATH"])
    try:
        ch = ChannelRepository(conn).get_by_id(channel_id)
        assert ch is not None and ch.active_session_id is not None
        return ch.active_session_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bubble-helper unit tests
# ---------------------------------------------------------------------------


class TestBubbleHelpers:
    def test_bubble_role_for_user(self):
        class M:
            source_type = "user"
            source_id = "alice"
            message_kind = "conversation"

        assert bubble_role(M()) == "user"

    def test_bubble_role_for_agent(self):
        class M:
            source_type = "agent"
            source_id = "b1"
            message_kind = "conversation"

        assert bubble_role(M()) == "agent"

    def test_bubble_role_for_orchestrator(self):
        class M:
            source_type = "orchestrator"
            source_id = "@orchestrator"
            message_kind = "conversation"

        assert bubble_role(M()) == "agent"

    def test_bubble_role_for_system_via_source_type(self):
        class M:
            source_type = "system"
            source_id = "sys"
            message_kind = "conversation"

        assert bubble_role(M()) == "system"

    def test_bubble_role_for_system_via_message_kind(self):
        class M:
            source_type = "agent"
            source_id = "b1"
            message_kind = "system"

        assert bubble_role(M()) == "system"

    def test_bubble_initials_user(self):
        class M:
            source_type = "user"
            source_id = "alice"
            message_kind = "conversation"

        assert bubble_initials(M()) == "U"

    def test_bubble_initials_agent_uses_display_name(self):
        class M:
            source_type = "agent"
            source_id = "binding-xyz"
            message_kind = "conversation"

        assert bubble_initials(M(), {"binding-xyz": "Atlas"}) == "A"

    def test_bubble_initials_agent_fallback(self):
        # source_id resolves to a non-empty string → initials from that.
        class M:
            source_type = "agent"
            source_id = "binding-xyz"
            message_kind = "conversation"

        assert bubble_initials(M(), {}) == "B"

    def test_bubble_initials_agent_empty_name_falls_back_to_a(self):
        class M:
            source_type = "agent"
            source_id = ""
            message_kind = "conversation"

        assert bubble_initials(M(), {}) == "A"

    def test_bubble_display_name_user(self):
        class M:
            source_type = "user"
            source_id = "alice"
            message_kind = "conversation"

        assert bubble_display_name(M()) == "alice"

    def test_bubble_display_name_agent_uses_participant_index(self):
        class M:
            source_type = "agent"
            source_id = "binding-xyz"
            message_kind = "conversation"

        name = bubble_display_name(M(), {"binding-xyz": "Atlas"})
        assert name == "Atlas"

    def test_bubble_display_name_agent_falls_back_to_short_id(self):
        class M:
            source_type = "agent"
            source_id = "abcdefghijklmnop"
            message_kind = "conversation"

        name = bubble_display_name(M(), {})
        # 8-char prefix + ellipsis.
        assert name == "abcdefgh…"

    def test_bubble_display_name_system(self):
        class M:
            source_type = "system"
            source_id = "sys"
            message_kind = "system"

        assert bubble_display_name(M()) == "System"

    def test_bubble_time_returns_hms(self):
        # 2026-01-01 12:34:56 UTC
        s = bubble_time(1767275696.0)
        assert len(s) == 8 and s[2] == ":" and s[5] == ":"

    def test_bubble_time_handles_none(self):
        assert bubble_time(None) == ""


# ---------------------------------------------------------------------------
# Bubble-render integration tests
# ---------------------------------------------------------------------------


class TestBubbleRender:
    def test_message_row_renders_bubble(self, app: Flask, client: FlaskClient, workspace_id: str):
        session_id = _create_session_with_channel(client, workspace_id, "bubble-1")
        # Post a user message via the standard route.
        resp = client.post(
            f"/sessions/{session_id}/message",
            data={"body": "Hello bubble", "user_id": "tester"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # /messages/list/<id> should now render with the bubble class.
        list_resp = client.get(f"/messages/list/{session_id}")
        assert list_resp.status_code == 200
        body = list_resp.data.decode("utf-8")
        assert "msg msg-own" in body
        assert "Hello bubble" in body
        # Legacy ``from-`` markup should no longer appear in the row.
        assert 'class="message from-user"' not in body
        # Avatar with the user initial.
        assert "msg-avatar-user" in body
        # The display name comes from source_id for users.
        assert "tester" in body

    def test_message_row_renders_agent_bubble(
        self, app: Flask, client: FlaskClient, workspace_id: str
    ):
        session_id = _create_session_with_channel(client, workspace_id, "bubble-2")
        # Insert an agent-shaped message directly via the routing service.
        from agent_workbench.db import get_connection
        from agent_workbench.models.channel import ChannelRepository
        from agent_workbench.models.session_extension import SessionExtensionRepository
        from agent_workbench.services.routing_service import (
            RoutingService,
            SOURCE_TYPE_AGENT,
            TARGET_TYPE_AGENT,
        )

        conn = get_connection(app.config["WORKBENCH_DB_PATH"])
        try:
            sess = SessionExtensionRepository(conn).get_by_id(session_id)
            ch = ChannelRepository(conn).get_by_id(
                next(c.channel_id for c in ChannelRepository(conn).list_by_workspace(
                    sess.workspace_id
                ) if c.active_session_id == session_id)
            )
            RoutingService(conn).route_message(
                workspace_id=sess.workspace_id,
                channel_id=ch.channel_id,
                source_type=SOURCE_TYPE_AGENT,
                source_id="binding-xyz",
                target_type=TARGET_TYPE_AGENT,
                target_id="web-user",
                message_kind="conversation",
                session_id=session_id,
                payload_ref=json.dumps({"envelope": "agent_reply", "body": "Hi from Atlas"}),
            )
        finally:
            conn.close()

        resp = client.get(f"/messages/list/{session_id}")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "msg msg-other" in body
        assert "Hi from Atlas" in body

    def test_dispatch_messages_are_hidden(self, app: Flask, client: FlaskClient, workspace_id: str):
        """Dispatch messages should not be rendered in the visible bubble list."""
        session_id = _create_session_with_channel(client, workspace_id, "bubble-3")
        from agent_workbench.db import get_connection
        from agent_workbench.models.channel import ChannelRepository
        from agent_workbench.models.session_extension import SessionExtensionRepository
        from agent_workbench.services.routing_service import (
            RoutingService,
            SOURCE_TYPE_ORCHESTRATOR,
            TARGET_TYPE_AGENT,
        )

        conn = get_connection(app.config["WORKBENCH_DB_PATH"])
        try:
            sess = SessionExtensionRepository(conn).get_by_id(session_id)
            ch_id = next(
                c.channel_id for c in ChannelRepository(conn).list_by_workspace(
                    sess.workspace_id
                ) if c.active_session_id == session_id
            )
            RoutingService(conn).route_message(
                workspace_id=sess.workspace_id,
                channel_id=ch_id,
                source_type=SOURCE_TYPE_ORCHESTRATOR,
                source_id="@orchestrator",
                target_type=TARGET_TYPE_AGENT,
                target_id="b1",
                message_kind="dispatch",
                session_id=session_id,
                payload_ref=json.dumps({"envelope": "chat_dispatch", "body": "internal"}),
            )
            RoutingService(conn).route_message(
                workspace_id=sess.workspace_id,
                channel_id=ch_id,
                source_type="user",
                source_id="tester",
                target_type="orchestrator",
                target_id="@orchestrator",
                message_kind="conversation",
                session_id=session_id,
                payload_ref=json.dumps({"envelope": "user_web_post", "body": "visible body"}),
            )
        finally:
            conn.close()

        resp = client.get(f"/messages/list/{session_id}")
        body = resp.data.decode("utf-8")
        assert "visible body" in body
        assert "internal" not in body


# ---------------------------------------------------------------------------
# SSE route tests
# ---------------------------------------------------------------------------


class TestSSEFallback:
    def test_no_event_stream_in_accept_redirects(
        self, client: FlaskClient, workspace_id: str
    ):
        session_id = _create_session_with_channel(client, workspace_id, "fb-1")
        # No Accept header for text/event-stream → 302 to polling endpoint.
        resp = client.get(
            f"/messages/stream/{session_id}?after=0",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        loc = resp.headers.get("Location", "")
        assert f"/messages/list/{session_id}/since" in loc
        assert "after=0" in loc

    def test_html_accept_redirects_to_polling(
        self, client: FlaskClient, workspace_id: str
    ):
        session_id = _create_session_with_channel(client, workspace_id, "fb-2")
        resp = client.get(
            f"/messages/stream/{session_id}",
            headers={"Accept": "text/html"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_event_stream_accept_returns_sse(
        self, client: FlaskClient, workspace_id: str
    ):
        session_id = _create_session_with_channel(client, workspace_id, "sse-1")
        resp = client.get(
            f"/messages/stream/{session_id}?after=0",
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        # First frame is a keep-alive comment.
        first = next(resp.response)
        assert b"connected" in first

    def test_event_stream_emits_message_frame_for_new_row(
        self, app: Flask, client: FlaskClient, workspace_id: str
    ):
        """A message that exists before the stream starts is emitted once
        when ``after`` is older than the message's ``created_at``."""
        session_id = _create_session_with_channel(client, workspace_id, "sse-2")
        # Insert the message BEFORE opening the stream, using the same
        # code path the live app uses (the per-request DB connection is
        # committed in a separate connection; SQLite WAL is used).
        db_path = app.config["WORKBENCH_DB_PATH"]
        conn = get_connection(db_path)
        try:
            from agent_workbench.models.channel import ChannelRepository
            from agent_workbench.models.session_extension import SessionExtensionRepository
            from agent_workbench.services.routing_service import (
                RoutingService,
                SOURCE_TYPE_USER,
                TARGET_TYPE_ORCHESTRATOR,
            )

            sess = SessionExtensionRepository(conn).get_by_id(session_id)
            ch = ChannelRepository(conn).get_by_id(
                next(
                    c.channel_id
                    for c in ChannelRepository(conn).list_by_workspace(
                        sess.workspace_id
                    )
                    if c.active_session_id == session_id
                )
            )
            RoutingService(conn).route_message(
                workspace_id=sess.workspace_id,
                channel_id=ch.channel_id,
                source_type=SOURCE_TYPE_USER,
                source_id="tester",
                target_type=TARGET_TYPE_ORCHESTRATOR,
                target_id="@orchestrator",
                message_kind="conversation",
                session_id=session_id,
                payload_ref=json.dumps({"envelope": "user_web_post", "body": "Live update"}),
            )
        finally:
            conn.close()

        # Open the stream with after=0. The generator emits the message
        # frame on its first tick (before any sleep).
        resp = client.get(
            f"/messages/stream/{session_id}?after=0",
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status_code == 200
        first = next(resp.response)
        assert b"connected" in first
        # The message frame should arrive within the first few ticks
        # of the generator. We don't loop indefinitely because the
        # test client's response iteration can block on the next
        # ``time.sleep(poll_interval)``.
        chunks = [first]
        for _ in range(5):
            try:
                chunks.append(next(resp.response))
            except StopIteration:
                break
        joined = b"".join(chunks)
        assert b"event: message" in joined, (
            f"event: message not found in first 6 chunks:\n{joined!r}"
        )
        assert b"Live update" in joined
        # The frame is JSON-encoded; verify the structure.
        data_lines = [
            line for line in joined.split(b"\n") if line.startswith(b"data: ")
        ]
        assert data_lines, "expected at least one data: line in the SSE stream"
        payload = json.loads(data_lines[0][len(b"data: ") :].decode("utf-8"))
        assert "html" in payload and "id" in payload
        assert "msg-own" in payload["html"]
        assert "Live update" in payload["html"]

    def test_event_stream_uses_after_cursor(
        self, client: FlaskClient, workspace_id: str
    ):
        """Messages older than ``after`` are not re-emitted on (re)connect."""
        session_id = _create_session_with_channel(client, workspace_id, "sse-cursor")
        # Insert one message first.
        db_path = client.application.config["WORKBENCH_DB_PATH"]
        conn = get_connection(db_path)
        try:
            from agent_workbench.models.channel import ChannelRepository
            from agent_workbench.models.session_extension import SessionExtensionRepository
            from agent_workbench.services.routing_service import (
                RoutingService,
                SOURCE_TYPE_USER,
                TARGET_TYPE_ORCHESTRATOR,
            )

            sess = SessionExtensionRepository(conn).get_by_id(session_id)
            ch = ChannelRepository(conn).get_by_id(
                next(
                    c.channel_id
                    for c in ChannelRepository(conn).list_by_workspace(
                        sess.workspace_id
                    )
                    if c.active_session_id == session_id
                )
            )
            m = RoutingService(conn).route_message(
                workspace_id=sess.workspace_id,
                channel_id=ch.channel_id,
                source_type=SOURCE_TYPE_USER,
                source_id="tester",
                target_type=TARGET_TYPE_ORCHESTRATOR,
                target_id="@orchestrator",
                message_kind="conversation",
                session_id=session_id,
                payload_ref=json.dumps({"envelope": "user_web_post", "body": "old"}),
            )
            after_ts = m.created_at
        finally:
            conn.close()

        # Open a stream with after > message timestamp; the existing
        # message must not be re-emitted. We give the generator a few
        # ticks (heartbeat is configured to 0.3s in the app fixture),
        # then assert no "event: message" frame ever appears in the
        # output.
        resp = client.get(
            f"/messages/stream/{session_id}?after={after_ts + 0.001}",
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status_code == 200
        first = next(resp.response)
        assert b"connected" in first
        # Drain a handful of chunks; none should contain a message frame.
        drained = [first]
        for _ in range(10):
            try:
                drained.append(next(resp.response))
            except StopIteration:
                break
        joined = b"".join(drained)
        assert b"event: message" not in joined
        assert b'"old"' not in joined

    def test_event_stream_unknown_session_returns_404(
        self, client: FlaskClient
    ):
        resp = client.get(
            "/messages/stream/does-not-exist",
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status_code == 404

    def test_event_stream_sets_nginx_header(
        self, client: FlaskClient, workspace_id: str
    ):
        session_id = _create_session_with_channel(client, workspace_id, "sse-hdr")
        resp = client.get(
            f"/messages/stream/{session_id}",
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status_code == 200
        # X-Accel-Buffering: no prevents nginx from buffering the stream.
        assert resp.headers.get("X-Accel-Buffering") == "no"
        assert resp.headers.get("Cache-Control") == "no-cache"


# ---------------------------------------------------------------------------
# Polling-fallback stays alive
# ---------------------------------------------------------------------------


class TestPollingFallback:
    def test_polling_since_returns_html(self, client: FlaskClient, workspace_id: str):
        session_id = _create_session_with_channel(client, workspace_id, "poll-1")
        client.post(
            f"/sessions/{session_id}/message",
            data={"body": "polled message", "user_id": "tester"},
            follow_redirects=False,
        )
        resp = client.get(f"/messages/list/{session_id}/since?after=0")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert "html" in payload and "next_after" in payload
        assert "msg-own" in payload["html"]
        assert "polled message" in payload["html"]
        assert payload["next_after"] > 0
