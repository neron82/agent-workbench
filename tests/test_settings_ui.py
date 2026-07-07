"""Integration tests for settings UI and chat participation flow."""

from pathlib import Path

import pytest

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.secret_store import load_saved_secrets
from agent_workbench.web import create_app


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "ui.db"
    monkeypatch.setenv("WORKBENCH_SECRETS_FILE", str(tmp_path / ".workbench.secrets.env"))
    conn = get_connection(str(db_path))
    apply_migrations(conn)
    conn.close()
    application = create_app(db_path=str(db_path))
    application.config.update(TESTING=True, WORKBENCH_AGENT_RESPONSE_MODE="sync")
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _boot_workspace(client):
    client.get("/channels")
    conn = get_connection(client.application.config["WORKBENCH_DB_PATH"])
    try:
        workspace = WorkspaceRepository(conn).get_default("default")
        assert workspace is not None
        return workspace.workspace_id
    finally:
        conn.close()


def _latest_agent_profile_id(client):
    conn = get_connection(client.application.config["WORKBENCH_DB_PATH"])
    try:
        profiles = ProfileService(conn).list_profiles()
        assert profiles
        return profiles[0].agent_profile_id
    finally:
        conn.close()


def _create_channel_with_session(client, workspace_id):
    response = client.post(
        "/channels",
        data={
            "workspace_id": workspace_id,
            "channel_kind": "chat",
            "title": "ui-chat",
            "create_session": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    channel_id = response.headers["Location"].rsplit("/", 1)[-1]
    conn = get_connection(client.application.config["WORKBENCH_DB_PATH"])
    try:
        channel = ChannelRepository(conn).get_by_id(channel_id)
        assert channel is not None
        assert channel.active_session_id is not None
        return channel_id, channel.active_session_id
    finally:
        conn.close()


def test_settings_pages_render_and_seeded_provider_visible(client):
    response = client.get("/settings/providers")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Mock Provider" in body
    assert "Settings" in body
    assert "stored locally" in body
    assert "OpenAI" in body  # preset button
    assert "Ollama Cloud" in body  # preset button
    assert "Custom" in body  # preset button


def test_test_and_fetch_models_rejects_empty_endpoint(client):
    response = client.post(
        "/settings/providers/test-and-fetch-models",
        content_type="application/json",
        data='{"endpoint_url": "", "api_key": ""}',
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is False
    assert "No endpoint URL" in data["error"]


def test_test_and_fetch_models_returns_error_on_unreachable(client):
    response = client.post(
        "/settings/providers/test-and-fetch-models",
        content_type="application/json",
        data='{"endpoint_url": "http://127.0.0.1:1/v1", "api_key": "test"}',
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is False
    # Should get a connection refused error
    assert data["error"]


def test_test_and_fetch_models_parses_openai_style_response(client, monkeypatch):
    import json
    import urllib.request

    original_urlopen = urllib.request.urlopen

    def fake_urlopen(req, **kw):
        class FakeResp:
            def read(self):
                return json.dumps({
                    "data": [
                        {"id": "gpt-4o", "object": "model"},
                        {"id": "gpt-4o-mini", "object": "model"},
                        {"id": "gpt-4.1", "object": "model"},
                    ]
                }).encode("utf-8")

            def __enter__(self):
                return FakeResp()

            def __exit__(self, *a):
                pass

        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    response = client.post(
        "/settings/providers/test-and-fetch-models",
        content_type="application/json",
        data=json.dumps({
            "endpoint_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
        }),
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert "gpt-4o" in data["models"]
    assert "gpt-4o-mini" in data["models"]


def test_test_and_fetch_models_parses_ollama_style_response(client, monkeypatch):
    import json
    import urllib.request

    def fake_urlopen(req, **kw):
        class FakeResp:
            def read(self):
                return json.dumps({
                    "models": [
                        {"name": "llama3.1:8b"},
                        {"name": "qwen3.5:cloud"},
                        {"name": "nemotron-3-super"},
                    ]
                }).encode("utf-8")

            def __enter__(self):
                return FakeResp()

            def __exit__(self, *a):
                pass

        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    response = client.post(
        "/settings/providers/test-and-fetch-models",
        content_type="application/json",
        data=json.dumps({
            "endpoint_url": "https://ollama.com/v1",
            "api_key": "ollama-test",
        }),
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert "llama3.1:8b" in data["models"]
    assert "qwen3.5:cloud" in data["models"]


def test_test_and_fetch_models_no_auth_for_local_endpoint(client, monkeypatch):
    import json
    import urllib.request

    def fake_urlopen(req, **kw):
        # Verify no Authorization header is sent for local endpoint
        assert "Authorization" not in req.headers
        class FakeResp:
            def read(self):
                return json.dumps({
                    "data": [{"id": "local-model"}]
                }).encode("utf-8")

            def __enter__(self):
                return FakeResp()

            def __exit__(self, *a):
                pass

        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    response = client.post(
        "/settings/providers/test-and-fetch-models",
        content_type="application/json",
        data=json.dumps({
            "endpoint_url": "http://192.168.2.201:8080/v1",
            "api_key": "",
            "provider_kind": "openai_compatible",
        }),
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert "local-model" in data["models"]


def test_provider_secret_can_be_saved_via_settings_without_restart(client):
    response = client.post(
        "/settings/providers",
        data={
            "name": "Local OpenAI",
            "provider_kind": "openai_compatible",
            "endpoint_url": "http://127.0.0.1:9999/v1",
            "default_model": "local-model",
            "api_key_value": "dummy",
            "is_enabled": "1",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "stored locally" in body
    secrets = load_saved_secrets()
    assert secrets["WORKBENCH_PROVIDER_LOCAL_OPENAI_API_KEY"] == "dummy"


def test_can_create_agent_via_settings_and_chat_with_mock_provider(client):
    workspace_id = _boot_workspace(client)

    create_agent = client.post(
        "/settings/agents",
        data={
            "name": "Mock Helper",
            "provider_ref": "provider-mock-default",
            "function_ref": "role-assistant",
            "model_ref": "mock-model",
            "perspective_ref": "pragmatisch",
            "harness_ref": "hermes",
        },
        follow_redirects=True,
    )
    assert create_agent.status_code == 200
    assert b"Agent angelegt" in create_agent.data
    agent_profile_id = _latest_agent_profile_id(client)

    _, session_id = _create_channel_with_session(client, workspace_id)

    add_participant = client.post(
        f"/sessions/{session_id}/participants",
        data={"agent_profile_id": agent_profile_id, "participant_role": "member"},
        follow_redirects=True,
    )
    assert add_participant.status_code == 200
    assert b"Agent zum Chat hinzugef" in add_participant.data

    post_message = client.post(
        f"/sessions/{session_id}/message",
        data={"body": "Bitte kurz antworten.", "user_id": "tester"},
        follow_redirects=True,
    )
    assert post_message.status_code == 200
    body = post_message.data.decode("utf-8")
    assert "Bitte kurz antworten." in body
    assert "lokale Mock-Antwort" in body
    assert "Mock Helper" in body

    incremental = client.get(f"/messages/list/{session_id}/since?after=0")
    assert incremental.status_code == 200
    payload = incremental.get_json()
    assert "lokale Mock-Antwort" in payload["html"]
    assert payload["next_after"] > 0


def test_session_config_page_shows_auto_turns_ui(client):
    """The session config page must render the auto-turns form."""
    workspace_id = _boot_workspace(client)
    _, session_id = _create_channel_with_session(client, workspace_id)

    response = client.get(f"/sessions/{session_id}/config")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Auto Turns" in body
    assert "max_auto_turns" in body
    assert "max_tool_iterations" in body or "Tool Call Limit" in body


def test_session_config_update_max_auto_turns_via_ui(client):
    """POST to /sessions/<id>/max-auto-turns must persist the value."""
    workspace_id = _boot_workspace(client)
    _, session_id = _create_channel_with_session(client, workspace_id)

    response = client.post(
        f"/sessions/{session_id}/max-auto-turns",
        data={"max_auto_turns": "7"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Auto-turn limit updated to 7" in body or "Auto Turns" in body

    # Verify the value persisted
    conn = get_connection(client.application.config["WORKBENCH_DB_PATH"])
    try:
        row = conn.execute(
            "SELECT max_auto_turns FROM session_extensions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        assert row is not None
        assert row["max_auto_turns"] == 7
    finally:
        conn.close()


def test_session_view_header_shows_auto_turns_inline_form(client):
    """The session view header must render the inline turns form."""
    workspace_id = _boot_workspace(client)
    _, session_id = _create_channel_with_session(client, workspace_id)

    response = client.get(f"/sessions/{session_id}")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "max_auto_turns" in body
    # The inline number input should be present
    assert 'name="max_auto_turns"' in body
