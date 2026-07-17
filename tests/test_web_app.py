"""Tests for the Flask web layer.

Exercises the ``channels`` and ``sessions`` blueprints through Flask's
``test_client`` against a real, migrated SQLite database. The DB path
is parameterised by a session-scoped fixture so the suite uses one
fresh database per test session.

CSRF tokens are auto-injected by the ``client`` fixture so existing
route tests pass through enabled CSRF protection without modification.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.routed_message import RoutedMessageRepository
from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.web import create_app


def _extract_csrf_token(html: bytes) -> str:
    """Extract the CSRF token from a rendered page's meta tag."""
    match = re.search(
        rb'<meta\s+name="csrf-token"\s+content="([^"]+)"',
        html,
    )
    if not match:
        raise AssertionError("No csrf-token meta tag found in response")
    return match.group(1).decode("utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app_db_path(tmp_path_factory) -> Path:
    """One fresh database file per test session, with migrations applied."""
    path = tmp_path_factory.mktemp("web-app") / "workbench.db"
    conn = get_connection(str(path))
    apply_migrations(conn)
    conn.close()
    return path


@pytest.fixture()
def workspace_id(app_db_path: Path) -> str:
    """Insert a single workspace row into the session-scoped DB."""
    conn = get_connection(str(app_db_path))
    try:
        repo = WorkspaceRepository(conn)
        ws = repo.create(tenant_id="t1", name="Test Workspace", is_default=True)
        return ws.workspace_id
    finally:
        conn.close()


@pytest.fixture()
def app(app_db_path: Path) -> Iterator[Flask]:
    """Build a Flask app bound to the session-scoped test database."""
    application = create_app(db_path=str(app_db_path))
    application.config.update(TESTING=True)
    yield application


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    """Flask test client wired to the app fixture above.

    Auto-injects a valid CSRF token into every unsafe request so
    existing route tests pass through enabled CSRF protection.
    """
    client = app.test_client()

    # Seed the session with a CSRF token via a GET request.
    resp = client.get("/")
    assert resp.status_code == 200
    token = _extract_csrf_token(resp.data)

    original_open = client.open

    def _patched_open(*args, **kwargs):
        method = kwargs.get("method", "GET")
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            data = kwargs.get("data")
            json_data = kwargs.get("json")
            headers = dict(kwargs.get("headers", {}))

            if json_data is not None:
                headers.setdefault("X-CSRF-Token", token)
                kwargs["headers"] = headers
            elif data is not None and isinstance(data, dict):
                data = dict(data)
                data.setdefault("csrf_token", token)
                kwargs["data"] = data
            elif data is not None and not isinstance(data, dict):
                headers.setdefault("X-CSRF-Token", token)
                kwargs["headers"] = headers
            else:
                kwargs["data"] = {"csrf_token": token}
                headers.setdefault("X-CSRF-Token", token)
                kwargs["headers"] = headers
        return original_open(*args, **kwargs)

    client.open = _patched_open  # type: ignore[method-assign]
    return client


# ---------------------------------------------------------------------------
# Index / health
# ---------------------------------------------------------------------------


class TestIndex:
    def test_create_app_default_db_path_points_to_project_root(self):
        application = create_app()
        expected = Path(__file__).resolve().parents[1] / "workbench.db"
        actual = Path(application.config["WORKBENCH_DB_PATH"]).resolve()
        assert actual == expected.resolve()

    def test_root_returns_redirect_or_200(self, client: FlaskClient):
        # ``GET /`` is wired to ``channels.index`` which renders the
        # landing page (no redirect).
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200

    def test_root_followed_redirects_to_channels(self, client: FlaskClient):
        resp = client.get("/", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Start a new session" in resp.data

    def test_healthz(self, client: FlaskClient):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert b"ok=1" in resp.data


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class TestChannelList:
    def test_channels_list_renders(self, client: FlaskClient, workspace_id: str):
        resp = client.get(f"/channels?workspace_id={workspace_id}")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "Channels" in body
        # The grouped headings exist (or at least the kind labels do).
        assert "chat" in body and "research" in body and "work" in body

    def test_channels_list_empty_workspace_message(self, client: FlaskClient):
        # No workspace_id -> default workspace is used (if any); we just
        # confirm the page renders without 500.
        resp = client.get("/channels")
        assert resp.status_code == 200

    def test_channels_list_bootstraps_default_workspace(self, tmp_path: Path):
        db_path = tmp_path / "fresh-workbench.db"
        conn = get_connection(str(db_path))
        apply_migrations(conn)
        conn.close()

        application = create_app(db_path=str(db_path))
        application.config.update(TESTING=True)
        client = application.test_client()

        resp = client.get("/channels")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "Create a new channel" in body
        assert "No chat channels yet." in body

        conn = get_connection(str(db_path))
        try:
            repo = WorkspaceRepository(conn)
            default = repo.get_default(tenant_id="default")
            assert default is not None
            assert default.name == "Default Workspace"
            assert len(repo.list_all()) == 1
        finally:
            conn.close()


class TestCreateChannel:
    def test_post_creates_channel(self, client: FlaskClient, workspace_id: str):
        resp = client.post(
            "/channels",
            data={
                "workspace_id": workspace_id,
                "channel_kind": "chat",
                "title": "general",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/channels/" in location

        # The redirected channel view should render and show the title.
        followed = client.get(location)
        assert followed.status_code == 200
        assert b"general" in followed.data

    def test_post_creates_channel_with_initial_session(
        self, client: FlaskClient, workspace_id: str
    ):
        resp = client.post(
            "/channels",
            data={
                "workspace_id": workspace_id,
                "channel_kind": "research",
                "title": "deep-dive",
                "create_session": "1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Channel should now have a research session linked.
        body = resp.data.decode("utf-8")
        assert "research" in body

    def test_post_invalid_channel_kind_returns_400(
        self, client: FlaskClient, workspace_id: str
    ):
        resp = client.post(
            "/channels",
            data={
                "workspace_id": workspace_id,
                "channel_kind": "bogus",
                "title": "x",
            },
        )
        assert resp.status_code == 400


class TestShowChannel:
    def test_get_existing_channel(self, client: FlaskClient, workspace_id: str):
        # Create a channel first.
        created = client.post(
            "/channels",
            data={
                "workspace_id": workspace_id,
                "channel_kind": "chat",
                "title": "showme",
                "create_session": "1",
            },
            follow_redirects=False,
        )
        assert created.status_code == 302
        location = created.headers["Location"]
        resp = client.get(location)
        assert resp.status_code == 200
        assert b"showme" in resp.data
        assert b"Active session" in resp.data or b"session" in resp.data

    def test_get_missing_channel_returns_404(self, client: FlaskClient):
        resp = client.get("/channels/does-not-exist")
        assert resp.status_code == 404


class TestForkChannel:
    def test_fork_creates_child_session(
        self, client: FlaskClient, workspace_id: str
    ):
        # 1. Create a chat channel with an initial session.
        create = client.post(
            "/channels",
            data={
                "workspace_id": workspace_id,
                "channel_kind": "chat",
                "title": "to-fork",
                "create_session": "1",
            },
            follow_redirects=False,
        )
        assert create.status_code == 302
        channel_url = create.headers["Location"]
        channel_id = channel_url.rsplit("/", 1)[-1]

        # 2. Fork it to a research session.
        fork_resp = client.post(
            f"/channels/{channel_id}/fork",
            data={
                "new_session_type": "research",
                "fork_reason": "Promote to structured investigation",
                "initiated_by": "user",
            },
            follow_redirects=False,
        )
        assert fork_resp.status_code == 302
        child_location = fork_resp.headers["Location"]
        assert "/sessions/" in child_location

        # 3. The child session view should show research and the new status.
        child_resp = client.get(child_location)
        assert child_resp.status_code == 200
        body = child_resp.data.decode("utf-8")
        assert "research" in body
        # The fork form is GET-only; the POST path goes straight to the
        # child session, so the form heading should NOT be there.
        assert "Fork channel" not in body

    def test_fork_get_renders_form(
        self, client: FlaskClient, workspace_id: str
    ):
        create = client.post(
            "/channels",
            data={
                "workspace_id": workspace_id,
                "channel_kind": "chat",
                "title": "fork-form",
                "create_session": "1",
            },
            follow_redirects=False,
        )
        channel_id = create.headers["Location"].rsplit("/", 1)[-1]
        resp = client.get(f"/channels/{channel_id}/fork")
        assert resp.status_code == 200
        assert b"Fork channel" in resp.data
        assert b"new_session_type" in resp.data

    def test_fork_invalid_type_returns_400(
        self, client: FlaskClient, workspace_id: str
    ):
        create = client.post(
            "/channels",
            data={
                "workspace_id": workspace_id,
                "channel_kind": "chat",
                "title": "fork-bad",
                "create_session": "1",
            },
            follow_redirects=False,
        )
        channel_id = create.headers["Location"].rsplit("/", 1)[-1]
        resp = client.post(
            f"/channels/{channel_id}/fork",
            data={"new_session_type": "bogus"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _create_channel_with_session(
    client: FlaskClient, workspace_id: str, kind: str = "chat", title: str = "sc"
) -> tuple[str, str]:
    """Helper: create a channel with a starter session, return (channel_id, session_id)."""
    from agent_workbench.db import get_connection
    from agent_workbench.models.channel import ChannelRepository

    create = client.post(
        "/channels",
        data={
            "workspace_id": workspace_id,
            "channel_kind": kind,
            "title": title,
            "create_session": "1",
        },
        follow_redirects=False,
    )
    assert create.status_code == 302
    channel_id = create.headers["Location"].rsplit("/", 1)[-1]

    db_path = client.application.config["WORKBENCH_DB_PATH"]
    conn = get_connection(str(db_path))
    try:
        ch = ChannelRepository(conn).get_by_id(channel_id)
        assert ch is not None and ch.active_session_id is not None
        return channel_id, ch.active_session_id
    finally:
        conn.close()


class TestSessionView:
    def test_get_session(self, client: FlaskClient, workspace_id: str):
        _, session_id = _create_channel_with_session(
            client, workspace_id, kind="chat", title="sv"
        )
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "chat" in body
        assert "Type a message" in body

    def test_get_missing_session_returns_404(self, client: FlaskClient):
        resp = client.get("/sessions/does-not-exist")
        assert resp.status_code == 404


class TestPostMessage:
    def test_post_message_routes_via_default_path(
        self, client: FlaskClient, workspace_id: str
    ):
        from agent_workbench.db import get_connection
        from agent_workbench.services.routing_service import RoutingService

        _, session_id = _create_channel_with_session(
            client, workspace_id, kind="chat", title="pm"
        )

        resp = client.post(
            f"/sessions/{session_id}/message",
            data={"body": "Hello from tests", "user_id": "tester"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with client.session_transaction() as browser_session:
            expected_user_id = browser_session["workbench_user_id"]

        # The routing service should now have one message on this session.
        db_path = client.application.config["WORKBENCH_DB_PATH"]
        conn = get_connection(str(db_path))
        try:
            routing = RoutingService(conn)
            messages = routing.get_messages_by_session(session_id)
            assert len(messages) == 1
            m = messages[0]
            assert m.source_type == "user"
            assert m.source_id == expected_user_id
            assert m.target_type == "orchestrator"
            assert m.target_id == "@orchestrator"
            assert m.message_kind == "conversation"
            assert m.payload_ref and "Hello from tests" in m.payload_ref
        finally:
            conn.close()

    def test_post_empty_message_redirects_with_flash(
        self, client: FlaskClient, workspace_id: str
    ):
        _, session_id = _create_channel_with_session(
            client, workspace_id, kind="chat", title="empty"
        )
        resp = client.post(
            f"/sessions/{session_id}/message",
            data={"body": "   "},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"cannot be empty" in resp.data


class TestUpdateStatus:
    def test_update_status_changes_value(
        self, client: FlaskClient, workspace_id: str
    ):
        from agent_workbench.db import get_connection
        from agent_workbench.models.session_extension import SessionExtensionRepository

        _, session_id = _create_channel_with_session(
            client, workspace_id, kind="work", title="status"
        )
        resp = client.post(
            f"/sessions/{session_id}/status",
            data={"status": "waiting_review"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        conn = get_connection(client.application.config["WORKBENCH_DB_PATH"])
        try:
            sess = SessionExtensionRepository(conn).get_by_id(session_id)
            assert sess is not None
            assert sess.status == "waiting_review"
        finally:
            conn.close()

    def test_update_status_invalid_returns_400(
        self, client: FlaskClient, workspace_id: str
    ):
        _, session_id = _create_channel_with_session(
            client, workspace_id, kind="chat", title="status-bad"
        )
        resp = client.post(
            f"/sessions/{session_id}/status",
            data={"status": "bogus"},
        )
        assert resp.status_code == 400


def _seed_browser_session(db_path: str, workspace_id: str, title: str, body: str):
    conn = get_connection(db_path)
    try:
        channel = ChannelRepository(conn).create(
            workspace_id=workspace_id, channel_kind="chat", title=title,
        )
        session = SessionExtensionRepository(conn).create(
            workspace_id=workspace_id, session_type="chat", title=title,
        )
        ChannelRepository(conn).update_active_session(
            channel.channel_id, active_session_id=session.session_id,
        )
        RoutedMessageRepository(conn).create(
            workspace_id=workspace_id,
            channel_id=channel.channel_id,
            session_id=session.session_id,
            source_type="user",
            source_id="user",
            target_type="orchestrator",
            target_id="@orchestrator",
            message_kind="conversation",
            payload_ref='{"body": "' + body + '"}',
        )
        return session.session_id
    finally:
        conn.close()


class TestWorkspaceDashboard:
    def test_create_workspace_is_visible_in_switcher(self, client: FlaskClient):
        response = client.post(
            "/workspaces", data={"name": "Client Alpha"}, follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Client Alpha" in response.data
        assert b"Workspace" in response.data

    def test_dashboard_searches_message_content_with_workspace_scope(
        self, client: FlaskClient, workspace_id: str
    ):
        db_path = client.application.config["WORKBENCH_DB_PATH"]
        matching_id = _seed_browser_session(
            db_path, workspace_id, "Investigation", "needle in the transcript",
        )
        _seed_browser_session(db_path, workspace_id, "Unrelated", "other content")

        conn = get_connection(db_path)
        try:
            other = WorkspaceRepository(conn).create(
                tenant_id="t2", name="Other Workspace",
            )
        finally:
            conn.close()
        _seed_browser_session(db_path, other.workspace_id, "Leak", "needle elsewhere")

        response = client.get(
            f"/?workspace_id={workspace_id}&q=needle&view=table",
        )
        body = response.data.decode("utf-8")
        assert response.status_code == 200
        assert matching_id in body
        assert "Investigation" in body
        assert "Unrelated" not in body
        assert "Leak" not in body
        assert "Session browser" in body
