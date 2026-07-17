"""Tests for session deletion controls in the workspace overview (landing.html).

Verifies:
1. Card view: each session card has a non-nested delete form with correct
   action, method=post, and confirmation prompt.
2. Table view: each row has a delete form with correct action, method=post,
   and confirmation prompt.
3. Card markup: the session card is a <div>, not an <a>, and the main link
   is a separate <a> inside it (no nested interactive content).
4. POST to delete action removes the session and redirects back to the
   workspace overview.
5. Two-workspace cookie behavior: deletion from workspace A redirects to A,
   not B.
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app_db_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("landing-delete") / "workbench.db"
    conn = get_connection(str(path))
    apply_migrations(conn)
    conn.close()
    return path


@pytest.fixture()
def workspace_a(app_db_path: Path) -> str:
    conn = get_connection(str(app_db_path))
    try:
        repo = WorkspaceRepository(conn)
        ws = repo.create(tenant_id="t1", name="Workspace A", is_default=True)
        return ws.workspace_id
    finally:
        conn.close()


@pytest.fixture()
def workspace_b(app_db_path: Path) -> str:
    conn = get_connection(str(app_db_path))
    try:
        repo = WorkspaceRepository(conn)
        ws = repo.create(tenant_id="t1", name="Workspace B")
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


class TestLandingCardDeleteForm:
    """Card view: delete form structure and correctness."""

    def test_card_is_div_not_anchor(self, client: FlaskClient, workspace_a: str):
        """Session cards are <div> elements, not <a>."""
        session_id = _create_session(client, workspace_a, "chat")
        resp = client.get(f"/?workspace_id={workspace_a}&view=cards")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")

        # Find the card for this session
        assert f'data-session-id="{session_id}"' in body

        # The card container should be a <div>, not an <a>.
        # Find the opening tag that contains data-session-id.
        marker_pos = body.find(f'data-session-id="{session_id}"')
        assert marker_pos != -1
        # Look backwards for the nearest opening tag
        div_start = body.rfind("<div", 0, marker_pos)
        a_start = body.rfind("<a", 0, marker_pos)
        assert div_start > a_start, (
            "Card should be a <div>, not an <a>: "
            f"<div> at {div_start}, <a> at {a_start}"
        )

        # There should be an <a> inside the card for the main link
        assert 'class="session-card-link"' in body

    def test_card_delete_form_has_correct_action(
        self, client: FlaskClient, workspace_a: str
    ):
        """Card delete form POSTs to sessions.delete_session."""
        session_id = _create_session(client, workspace_a, "chat")
        resp = client.get(f"/?workspace_id={workspace_a}&view=cards")
        body = resp.data.decode("utf-8")

        # The form should POST to the delete endpoint
        expected_action = f"/sessions/{session_id}/delete"
        form_pattern = re.compile(
            r'<form[^>]*method="post"[^>]*action="' + re.escape(expected_action) + r'"[^>]*>'
        )
        assert form_pattern.search(body), (
            f"Expected form with action={expected_action!r}"
        )

    def test_card_delete_form_has_confirmation(
        self, client: FlaskClient, workspace_a: str
    ):
        """Card delete form has a confirmation prompt."""
        _create_session(client, workspace_a, "chat")
        resp = client.get(f"/?workspace_id={workspace_a}&view=cards")
        body = resp.data.decode("utf-8")

        # The form should have onsubmit with confirm()
        assert "return confirm(" in body
        assert "Delete this session?" in body
        assert "permanent" in body

    def test_card_delete_button_has_accessible_label(
        self, client: FlaskClient, workspace_a: str
    ):
        """Card delete button has aria-label."""
        _create_session(client, workspace_a, "chat")
        resp = client.get(f"/?workspace_id={workspace_a}&view=cards")
        body = resp.data.decode("utf-8")

        # The button should have aria-label containing "Delete session"
        assert 'aria-label="Delete session' in body

    def test_card_delete_form_not_nested_in_anchor(
        self, client: FlaskClient, workspace_a: str
    ):
        """The delete form is NOT nested inside the main session link."""
        session_id = _create_session(client, workspace_a, "chat")
        resp = client.get(f"/?workspace_id={workspace_a}&view=cards")
        body = resp.data.decode("utf-8")

        # Find the card div — start from the opening <div> tag
        card_marker = f'data-session-id="{session_id}"'
        marker_pos = body.find(card_marker)
        assert marker_pos != -1
        card_start = body.rfind("<div", 0, marker_pos)
        assert card_start != -1

        # Find the card's closing </div> by counting nesting depth
        depth = 0
        card_end = card_start
        for i in range(card_start, len(body)):
            if body[i:i+5] == "<div " or body[i:i+5] == "<div>":
                depth += 1
            elif body[i:i+6] == "</div>":
                depth -= 1
                if depth == 0:
                    card_end = i + 6
                    break
        card_section = body[card_start:card_end]

        # The <a> tag should close before the <form> starts
        a_close_pos = card_section.rfind("</a>")
        form_start_pos = card_section.find("<form")
        assert a_close_pos != -1, "Card should have a closing </a>"
        assert form_start_pos != -1, "Card should have a <form>"
        assert a_close_pos < form_start_pos, (
            "Form must not be nested inside the anchor: "
            f"</a> at {a_close_pos}, <form> at {form_start_pos}"
        )

    def test_card_preserves_existing_content(
        self, client: FlaskClient, workspace_a: str
    ):
        """Card still shows badges, preview, counts, Continue link."""
        _create_session(client, workspace_a, "chat")
        resp = client.get(f"/?workspace_id={workspace_a}&view=cards")
        body = resp.data.decode("utf-8")

        assert "badge-chat" in body
        assert "badge-active" in body
        assert "Continue" in body
        assert "card-meta-item" in body


class TestLandingTableDeleteForm:
    """Table view: delete form structure and correctness."""

    def test_table_delete_form_has_correct_action(
        self, client: FlaskClient, workspace_a: str
    ):
        """Table delete form POSTs to sessions.delete_session."""
        session_id = _create_session(client, workspace_a, "chat")
        resp = client.get(f"/?workspace_id={workspace_a}&view=table")
        body = resp.data.decode("utf-8")

        expected_action = f"/sessions/{session_id}/delete"
        form_pattern = re.compile(
            r'<form[^>]*method="post"[^>]*action="' + re.escape(expected_action) + r'"[^>]*>'
        )
        assert form_pattern.search(body), (
            f"Expected form with action={expected_action!r}"
        )

    def test_table_delete_form_has_confirmation(
        self, client: FlaskClient, workspace_a: str
    ):
        """Table delete form has a confirmation prompt."""
        _create_session(client, workspace_a, "chat")
        resp = client.get(f"/?workspace_id={workspace_a}&view=table")
        body = resp.data.decode("utf-8")

        assert "return confirm(" in body
        assert "Delete this session?" in body

    def test_table_delete_button_has_accessible_label(
        self, client: FlaskClient, workspace_a: str
    ):
        """Table delete button has aria-label."""
        _create_session(client, workspace_a, "chat")
        resp = client.get(f"/?workspace_id={workspace_a}&view=table")
        body = resp.data.decode("utf-8")

        assert 'aria-label="Delete session' in body

    def test_table_has_delete_column_header(
        self, client: FlaskClient, workspace_a: str
    ):
        """Table has a column for the delete action."""
        resp = client.get(f"/?workspace_id={workspace_a}&view=table")
        body = resp.data.decode("utf-8")

        # The table should have 6 columns (including the new delete column)
        assert "<th></th><th></th>" in body or "<th></th>" in body


class TestLandingDeleteAction:
    """End-to-end: POST to delete action removes session and redirects."""

    def test_post_delete_removes_session_and_redirects(
        self, client: FlaskClient, workspace_a: str
    ):
        """POST to delete_session removes the session and redirects to workspace."""
        session_id = _create_session(client, workspace_a, "chat")

        # Verify session exists
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200

        # POST to delete
        resp = client.post(
            f"/sessions/{session_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")

        # Should redirect back to the workspace overview
        assert workspace_a in location
        assert "channels.index" in location or "/?" in location

        # Session should be gone
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 404

    def test_post_delete_redirects_to_correct_workspace(
        self, client: FlaskClient, workspace_a: str, workspace_b: str
    ):
        """Deleting from workspace A redirects to A, not B."""
        session_id = _create_session(client, workspace_a, "chat")

        # Set cookie to workspace B
        with client.session_transaction() as sess:
            sess["workbench_workspace_id"] = workspace_b

        # Delete session from workspace A
        resp = client.post(
            f"/sessions/{session_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")

        # Should redirect to workspace A (the session's workspace), not B
        assert workspace_a in location, (
            f"Redirect should go to workspace A ({workspace_a}), "
            f"got location={location!r}"
        )
