"""Focused tests for session-level fork removal and Transfer promotion.

Proves:
* Session view has direct Transfer link with #transfer and no session dropdown Fork action.
* Config renders anchored canonical transfer form and correct action/fields.
* Obsolete session-fork GET/POST and fork-detail URLs return 404.
* No ``forks.*`` endpoints are registered.
* Removed source/templates are absent/unreferenced.
* Channel fork endpoint still exists, GET renders 200, and its template remains.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.workspace import Workspace, WorkspaceRepository
from agent_workbench.web import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(db: sqlite3.Connection, tmp_db: Path):
    flask_app = create_app(db_path=str(tmp_db))
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test"
    flask_app.config["WORKBENCH_DB_CONN"] = db
    yield flask_app


@pytest.fixture
def client(app):
    from tests.conftest import make_csrf_client
    return make_csrf_client(app)


@pytest.fixture
def workspace(db: sqlite3.Connection) -> Workspace:
    return WorkspaceRepository(db).create(tenant_id="tenant-1", name="Test WS")


@pytest.fixture
def session_id(db: sqlite3.Connection, workspace: Workspace) -> str:
    se = SessionExtensionRepository(db).create(
        workspace_id=workspace.workspace_id, session_type="chat"
    )
    return se.session_id


# ---------------------------------------------------------------------------
# 1) Session view: Transfer link with #transfer, no Fork action
# ---------------------------------------------------------------------------


class TestSessionViewTransferLink:
    def test_session_view_has_transfer_link_to_config_with_hash(
        self, client, session_id: str
    ):
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Must link to session_config with #transfer
        assert f"/sessions/{session_id}/config" in body
        assert "#transfer" in body
        # The link text should mention Transfer
        assert "Transfer" in body

    def test_session_view_has_no_fork_form_in_dropdown(
        self, client, session_id: str
    ):
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # The old Fork form/button referencing channels.fork_channel should be gone
        assert "channels.fork_channel" not in body
        # The old Fork button text should not appear in the dropdown
        assert "🔀 Fork" not in body

    def test_session_view_still_has_config_and_delete(
        self, client, session_id: str
    ):
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Config link still present
        assert f"/sessions/{session_id}/config" in body
        # Delete form still present
        assert f"/sessions/{session_id}/delete" in body
        assert "🗑 Delete" in body


# ---------------------------------------------------------------------------
# 2) Config page: anchored transfer form with correct action/fields
# ---------------------------------------------------------------------------


class TestSessionConfigTransferForm:
    def test_config_has_id_transfer_on_continuation_card(
        self, client, session_id: str
    ):
        resp = client.get(f"/sessions/{session_id}/config")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'id="transfer"' in body

    def test_config_transfer_form_has_correct_action(
        self, client, session_id: str
    ):
        resp = client.get(f"/sessions/{session_id}/config")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Form posts to sessions.transfer_session
        assert f"/sessions/{session_id}/transfer" in body

    def test_config_transfer_form_has_expected_fields(
        self, client, session_id: str
    ):
        resp = client.get(f"/sessions/{session_id}/config")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'name="title"' in body
        assert 'name="session_type"' in body
        assert 'name="context_summary"' in body
        # participant_ids checkboxes are rendered when participants exist
        # (the test session has no participants, so the checkbox block is empty)

    def test_config_transfer_form_has_submit_button(
        self, client, session_id: str
    ):
        resp = client.get(f"/sessions/{session_id}/config")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Create continuation" in body

    def test_config_uses_continuation_language_not_fork(
        self, client, session_id: str
    ):
        resp = client.get(f"/sessions/{session_id}/config")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Should use transfer/continuation language, not "Fork this session"
        assert "Fork this session" not in body
        assert "Continue" in body or "continuation" in body.lower()


# ---------------------------------------------------------------------------
# 3) Obsolete session-fork URLs return 404
# ---------------------------------------------------------------------------


class TestObsoleteForkUrlsReturn404:
    def test_get_session_fork_returns_404(self, client, session_id: str):
        resp = client.get(f"/sessions/{session_id}/fork")
        assert resp.status_code == 404

    def test_post_session_fork_returns_404(self, client, session_id: str):
        resp = client.post(
            f"/sessions/{session_id}/fork",
            data={"new_session_type": "research", "summary": "test"},
        )
        assert resp.status_code == 404

    def test_fork_detail_returns_404(self, client):
        resp = client.get(f"/forks/{uuid.uuid4().hex}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4) No forks.* endpoints registered
# ---------------------------------------------------------------------------


class TestNoForksBlueprintRegistered:
    def test_no_forks_in_url_map(self, client):
        """Verify no 'forks.*' endpoint names exist in the route map."""
        rules = [str(r) for r in client.application.url_map.iter_rules()]
        fork_rules = [r for r in rules if "fork" in r.lower()]
        # The only fork-related rules should be channel fork ones
        for r in fork_rules:
            assert "channels" in r, f"Unexpected non-channel fork rule: {r}"

    def test_no_forks_endpoint_names(self, client):
        """Verify no endpoint name starts with 'forks.'."""
        rules = list(client.application.url_map.iter_rules())
        fork_endpoints = [r.endpoint for r in rules if r.endpoint.startswith("forks.")]
        assert fork_endpoints == [], f"Unexpected forks endpoints: {fork_endpoints}"


# ---------------------------------------------------------------------------
# 5) Removed source/templates are absent/unreferenced
# ---------------------------------------------------------------------------


class TestRemovedFilesAbsent:
    def test_forks_py_deleted(self):
        path = Path(__file__).resolve().parents[1] / "src" / "agent_workbench" / "web" / "forks.py"
        assert not path.exists(), f"forks.py should have been deleted: {path}"

    def test_fork_form_html_deleted(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agent_workbench"
            / "web"
            / "templates"
            / "fork_form.html"
        )
        assert not path.exists(), f"fork_form.html should have been deleted: {path}"

    def test_fork_detail_html_deleted(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agent_workbench"
            / "web"
            / "templates"
            / "fork_detail.html"
        )
        assert not path.exists(), f"fork_detail.html should have been deleted: {path}"

    def test_fork_ui_test_deleted(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "tests"
            / "test_fork_ui.py"
        )
        assert not path.exists(), f"test_fork_ui.py should have been deleted: {path}"

    def test_app_py_no_longer_imports_forks(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agent_workbench"
            / "web"
            / "app.py"
        )
        content = path.read_text()
        assert "from agent_workbench.web.forks import forks_bp" not in content
        assert "app.register_blueprint(forks_bp)" not in content


# ---------------------------------------------------------------------------
# 6) Channel fork endpoint still works
# ---------------------------------------------------------------------------


class TestChannelForkStillWorks:
    def test_channel_fork_get_renders_200(self, client, workspace: Workspace):
        # Create a channel first
        resp = client.post(
            "/channels",
            data={
                "workspace_id": workspace.workspace_id,
                "channel_kind": "chat",
                "title": "fork-test-ch",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        channel_id = resp.headers["Location"].rsplit("/", 1)[-1]

        resp2 = client.get(f"/channels/{channel_id}/fork")
        assert resp2.status_code == 200
        body = resp2.get_data(as_text=True)
        assert "Fork channel" in body

    def test_channel_fork_template_remains(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agent_workbench"
            / "web"
            / "templates"
            / "channel_fork_form.html"
        )
        assert path.exists(), "channel_fork_form.html must still exist"

    def test_channel_fork_endpoint_in_url_map(self, client):
        rules = list(client.application.url_map.iter_rules())
        channel_fork_rules = [
            r for r in rules if r.endpoint == "channels.fork_channel"
        ]
        assert len(channel_fork_rules) == 1, (
            f"Expected 1 channel fork rule, got {len(channel_fork_rules)}"
        )

    def test_channel_fork_post_creates_fork(
        self, client, db: sqlite3.Connection, workspace: Workspace
    ):
        """Exercise a channel fork POST to prove it still works."""
        # Create a channel with an active session
        from agent_workbench.models.channel import ChannelRepository

        ch_repo = ChannelRepository(db)
        channel = ch_repo.create(
            workspace_id=workspace.workspace_id,
            channel_kind="chat",
            title="ch-fork-test",
        )
        # Create a session and link it as active
        se = SessionExtensionRepository(db).create(
            workspace_id=workspace.workspace_id, session_type="chat"
        )
        ch_repo.update_active_session(channel.channel_id, active_session_id=se.session_id)

        resp = client.post(
            f"/channels/{channel.channel_id}/fork",
            data={"new_session_type": "research"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/sessions/" in location

        # Verify a fork record was created
        row = db.execute(
            "SELECT COUNT(*) AS cnt FROM fork_records "
            "WHERE parent_session_id = ?",
            (se.session_id,),
        ).fetchone()
        assert row["cnt"] >= 1, "Expected at least one fork record from channel fork POST"
