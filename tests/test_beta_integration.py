"""Integration tests for beta workspace, team, and targeting flows."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.agent_profile import AgentProfileRepository
from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.provider import ProviderRepository
from agent_workbench.models.routed_message import RoutedMessageRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.participant_service import ParticipantService
from agent_workbench.services.team_service import TeamService
from agent_workbench.web.app import create_app


@pytest.fixture()
def beta_app(tmp_path: Path):
    db_path = tmp_path / "beta-integration.db"
    conn = get_connection(str(db_path))
    apply_migrations(conn)
    conn.close()
    app = create_app(db_path=str(db_path))
    app.config.update(TESTING=True, WORKBENCH_AGENT_RESPONSE_MODE="sync")
    return app, db_path


@pytest.fixture()
def client(beta_app):
    from tests.conftest import make_csrf_client
    return make_csrf_client(beta_app[0])


def _workspace(db_path: Path, name: str, *, default: bool = False):
    conn = get_connection(str(db_path))
    try:
        return WorkspaceRepository(conn).create(
            tenant_id="default", name=name, is_default=default
        )
    finally:
        conn.close()


def _session(db_path: Path, workspace_id: str, title: str = "Session"):
    conn = get_connection(str(db_path))
    try:
        channel = ChannelRepository(conn).create(
            workspace_id=workspace_id, channel_kind="chat", title=title
        )
        session = SessionExtensionRepository(conn).create(
            workspace_id=workspace_id, session_type="chat", title=title
        )
        ChannelRepository(conn).update_active_session(
            channel.channel_id, active_session_id=session.session_id
        )
        return session
    finally:
        conn.close()


def _mock_profiles(db_path: Path, session_id: str, names: list[str]):
    conn = get_connection(str(db_path))
    try:
        provider = ProviderRepository(conn).create(
            name="Mock beta provider", provider_kind="mock", default_model="mock"
        )
        profiles = []
        for name in names:
            profile = AgentProfileRepository(conn).create(
                name=name,
                provider_ref=provider.provider_id,
                model_ref="mock",
                harness_ref="hermes",
                capability_hints_json={"allowed_permission_classes": ["read_only"]},
            )
            ParticipantService(conn).add_participant(
                session_id=session_id,
                agent_profile_id=profile.agent_profile_id,
            )
            profiles.append(profile)
        return profiles
    finally:
        conn.close()


def test_session_resource_overrides_stale_workspace_cookie(client, beta_app):
    _, db_path = beta_app
    workspace_a = _workspace(db_path, "Project Alpha", default=True)
    workspace_b = _workspace(db_path, "Project Beta")
    session = _session(db_path, workspace_a.workspace_id)

    with client.session_transaction() as browser_session:
        browser_session["workbench_workspace_id"] = workspace_b.workspace_id

    response = client.get(f"/sessions/{session.session_id}")
    body = response.data.decode()
    assert response.status_code == 200
    assert f'value="{workspace_a.workspace_id}" selected' in body
    assert f'value="{workspace_b.workspace_id}" selected' not in body
    assert "Project Alpha" in body


def test_workspace_team_management_and_apply_flow(client, beta_app):
    _, db_path = beta_app
    workspace = _workspace(db_path, "Team Project", default=True)
    session = _session(db_path, workspace.workspace_id)
    profiles = _mock_profiles(db_path, session.session_id, ["Existing"])

    create = client.post(
        f"/workspaces/{workspace.workspace_id}/teams",
        data={"name": "Research Crew", "description": "Read-only investigators"},
        follow_redirects=False,
    )
    assert create.status_code == 302

    conn = get_connection(str(db_path))
    try:
        team = TeamService(conn).list_teams(workspace.workspace_id)[0]
        extra = AgentProfileRepository(conn).create(
            name="Researcher",
            harness_ref="hermes",
            capability_hints_json={"allowed_permission_classes": ["read_only"]},
        )
    finally:
        conn.close()

    add = client.post(
        f"/workspaces/{workspace.workspace_id}/teams/{team.team_id}/members",
        data={
            "agent_profile_id": extra.agent_profile_id,
            "role_label": "researcher",
            "sort_order": "2",
        },
        follow_redirects=False,
    )
    assert add.status_code == 302

    apply = client.post(
        f"/sessions/{session.session_id}/teams/{team.team_id}/apply",
        follow_redirects=False,
    )
    assert apply.status_code == 302

    conn = get_connection(str(db_path))
    try:
        active = ParticipantService(conn).list_active_participant_details(session.session_id)
        assert {row["agent_profile_id"] for row in active} == {
            profiles[0].agent_profile_id,
            extra.agent_profile_id,
        }
    finally:
        conn.close()


def test_cannot_apply_team_from_another_workspace(client, beta_app):
    _, db_path = beta_app
    workspace_a = _workspace(db_path, "A", default=True)
    workspace_b = _workspace(db_path, "B")
    session = _session(db_path, workspace_a.workspace_id)
    conn = get_connection(str(db_path))
    try:
        team = TeamService(conn).create_team(
            workspace_id=workspace_b.workspace_id, name="Foreign"
        )
    finally:
        conn.close()

    response = client.post(
        f"/sessions/{session.session_id}/teams/{team.team_id}/apply"
    )
    assert response.status_code == 404


def test_message_can_target_explicit_agent_subset(client, beta_app):
    _, db_path = beta_app
    workspace = _workspace(db_path, "Targeting", default=True)
    session = _session(db_path, workspace.workspace_id)
    _mock_profiles(db_path, session.session_id, ["Alpha", "Beta", "Gamma"])

    response = client.post(
        f"/sessions/{session.session_id}/message",
        data={
            "body": "Compare these findings",
            "target_agents": ["Alpha", "Gamma"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    conn = get_connection(str(db_path))
    try:
        messages = RoutedMessageRepository(conn).list_by_session(session.session_id)
        responders = {
            message.source_id for message in messages if message.source_type == "agent"
        }
        assert responders == {"Alpha", "Gamma"}
        dispatches = [message for message in messages if message.message_kind == "dispatch"]
        assert len(dispatches) == 2
        user_id = next(
            message.source_id for message in messages if message.source_type == "user"
        )
    finally:
        conn.close()

    rendered = client.get(f"/sessions/{session.session_id}").data.decode()
    assert f'<span class="msg-name">{user_id}</span>' not in rendered
    assert "You" in rendered


def test_session_view_exposes_permission_summary_and_target_controls(client, beta_app):
    _, db_path = beta_app
    workspace = _workspace(db_path, "Visible Policy", default=True)
    session = _session(db_path, workspace.workspace_id)
    _mock_profiles(db_path, session.session_id, ["Reader"])

    response = client.get(f"/sessions/{session.session_id}")
    body = response.data.decode()
    assert response.status_code == 200
    assert "read_only" in body
    assert 'name="target_agents"' in body
    assert "All agents" in body


def test_delete_empty_workspace_removes_owned_labels_and_teams(client, beta_app):
    _, db_path = beta_app
    workspace = _workspace(db_path, "Disposable", default=True)
    conn = get_connection(str(db_path))
    try:
        TeamService(conn).create_team(workspace_id=workspace.workspace_id, name="Temporary")
    finally:
        conn.close()

    response = client.post(
        f"/workspaces/{workspace.workspace_id}/delete", follow_redirects=False
    )
    assert response.status_code == 302

    conn = get_connection(str(db_path))
    try:
        assert WorkspaceRepository(conn).get_by_id(workspace.workspace_id) is None
        assert TeamService(conn).list_teams(workspace.workspace_id) == []
        assert conn.execute(
            "SELECT COUNT(*) FROM session_labels WHERE workspace_id = ?",
            (workspace.workspace_id,),
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
