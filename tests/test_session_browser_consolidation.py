"""Tests for session-browser consolidation.

Verifies that:
1. base.html nav links point to ``channels.index`` with ``workspace_id`` and ``type``
2. Active nav class works when endpoint is ``channels.index`` and ``request.args['type']`` matches
3. session_config.html back-link points to ``channels.index`` with workspace + type
4. ``channels.list_by_type`` redirects to ``channels.index`` preserving workspace/type
5. Invalid session types still abort 400
6. ``session_list.html`` template is deleted and no references remain
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.web import create_app


# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_web_app.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app_db_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("session-browser") / "workbench.db"
    conn = get_connection(str(path))
    apply_migrations(conn)
    conn.close()
    return path


@pytest.fixture()
def workspace_id(app_db_path: Path) -> str:
    conn = get_connection(str(app_db_path))
    try:
        repo = WorkspaceRepository(conn)
        ws = repo.create(tenant_id="t1", name="Test Workspace", is_default=True)
        return ws.workspace_id
    finally:
        conn.close()


@pytest.fixture()
def app(app_db_path: Path) -> Iterator[Flask]:
    application = create_app(db_path=str(app_db_path))
    application.config.update(TESTING=True)
    yield application


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    from tests.conftest import make_csrf_client
    return make_csrf_client(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_session(
    client: FlaskClient, workspace_id: str, session_type: str = "chat",
) -> str:
    """Create a channel + session, return the session_id."""
    create = client.post(
        "/channels",
        data={
            "workspace_id": workspace_id,
            "channel_kind": session_type,
            "title": f"test-{session_type}",
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
        return ch.active_session_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNavLinks:
    """base.html nav links must point to channels.index with type filter."""

    def test_nav_hrefs_use_channels_index(self, client: FlaskClient, workspace_id: str):
        """Chat/Research/Work links should target channels.index with type param."""
        resp = client.get(f"/?workspace_id={workspace_id}")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")

        # Each nav link should contain channels.index URL with type=...
        # We check for the URL pattern rather than full HTML snapshots.
        assert "channels.index" not in body  # Jinja source, not rendered
        # The rendered href should contain the type parameter
        assert "type=chat" in body
        assert "type=research" in body
        assert "type=work" in body

    def test_nav_active_class_on_matching_type(self, client: FlaskClient, workspace_id: str):
        """When on channels.index with type=chat, the Chat link should be active."""
        resp = client.get(f"/?workspace_id={workspace_id}&type=chat")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        # The nav-chat link should have the active class
        assert 'class="nav-chat active"' in body or 'class="nav-chat active' in body
        # The other links should NOT be active
        assert 'class="nav-research active"' not in body
        assert 'class="nav-work active"' not in body

    def test_nav_active_class_research(self, client: FlaskClient, workspace_id: str):
        resp = client.get(f"/?workspace_id={workspace_id}&type=research")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert 'class="nav-research active"' in body or 'class="nav-research active' in body
        assert 'class="nav-chat active"' not in body
        assert 'class="nav-work active"' not in body

    def test_active_type_prefills_new_session_picker(
        self, client: FlaskClient, workspace_id: str
    ):
        resp = client.get(f"/?workspace_id={workspace_id}&type=research")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert re.search(
            r'data-kind="research"[^>]*aria-checked="true"', body
        )
        assert re.search(
            r'name="channel_kind"[^>]*value="research"', body
        )

    def test_nav_active_class_work(self, client: FlaskClient, workspace_id: str):
        resp = client.get(f"/?workspace_id={workspace_id}&type=work")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert 'class="nav-work active"' in body or 'class="nav-work active' in body
        assert 'class="nav-chat active"' not in body
        assert 'class="nav-research active"' not in body

    def test_nav_no_active_when_type_is_all(self, client: FlaskClient, workspace_id: str):
        """When type=all (or no type), no nav link should be active."""
        resp = client.get(f"/?workspace_id={workspace_id}&type=all")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert 'class="nav-chat active"' not in body
        assert 'class="nav-research active"' not in body
        assert 'class="nav-work active"' not in body


class TestListByTypeRedirect:
    """channels.list_by_type must redirect to channels.index."""

    def test_redirects_to_index_with_type(self, client: FlaskClient, workspace_id: str):
        resp = client.get(
            f"/sessions/type/chat?workspace_id={workspace_id}",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "workspace_id" in location
        assert "type=chat" in location

    def test_redirect_preserves_workspace(self, client: FlaskClient, workspace_id: str):
        resp = client.get(
            f"/sessions/type/research?workspace_id={workspace_id}",
            follow_redirects=False,
        )
        location = resp.headers.get("Location", "")
        assert workspace_id in location
        assert "type=research" in location

    def test_redirect_followed_renders_landing(self, client: FlaskClient, workspace_id: str):
        resp = client.get(
            f"/sessions/type/work?workspace_id={workspace_id}",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Should render the landing page, not session_list.html
        assert b"Session browser" in resp.data or b"Start a new session" in resp.data

    def test_invalid_type_returns_400(self, client: FlaskClient):
        resp = client.get("/sessions/type/bogus")
        assert resp.status_code == 400

    def test_invalid_type_returns_400_with_workspace(self, client: FlaskClient, workspace_id: str):
        resp = client.get(f"/sessions/type/bogus?workspace_id={workspace_id}")
        assert resp.status_code == 400


class TestSessionConfigBackLink:
    """session_config.html back-link must point to channels.index."""

    def test_back_link_uses_channels_index(self, client: FlaskClient, workspace_id: str):
        session_id = _create_session(client, workspace_id, "research")
        resp = client.get(f"/sessions/{session_id}/config")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")

        # The back link should point to channels.index with type=research
        # and the workspace_id
        assert "type=research" in body
        assert workspace_id in body
        # Should NOT reference list_by_type
        assert "list_by_type" not in body


class TestSessionListTemplateRemoved:
    """session_list.html must be deleted and unreferenced."""

    def test_session_list_template_file_gone(self):
        project_root = Path(__file__).resolve().parents[1]
        template_path = (
            project_root
            / "src"
            / "agent_workbench"
            / "web"
            / "templates"
            / "session_list.html"
        )
        assert not template_path.exists(), (
            f"{template_path} should have been deleted"
        )

    def test_no_source_references_to_session_list(self):
        """No source file should reference ``session_list.html``."""
        source_root = Path(__file__).resolve().parents[1] / "src"
        references = [
            path
            for path in source_root.rglob("*")
            if path.is_file()
            and "session_list.html" in path.read_text(errors="ignore")
        ]
        assert references == []
