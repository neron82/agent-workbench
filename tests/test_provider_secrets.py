"""Regression tests for provider secret handling in live chat runtime."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.agent_runtime_service import AgentRuntimeService
from agent_workbench.services.participant_service import ParticipantService
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.provider_service import ProviderService
from agent_workbench.services.routing_service import RoutingService
from agent_workbench.services.session_service import SessionService


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_openai_compatible_provider_can_use_saved_secret_without_process_restart(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "runtime.db"
    secrets_path = tmp_path / ".workbench.secrets.env"
    secrets_path.write_text("LOCAL_DUMMY_KEY=dummy\n", encoding="utf-8")
    monkeypatch.setenv("WORKBENCH_SECRETS_FILE", str(secrets_path))
    monkeypatch.delenv("LOCAL_DUMMY_KEY", raising=False)

    conn = get_connection(str(db_path))
    apply_migrations(conn)

    workspace_id = WorkspaceRepository(conn).create(tenant_id="t1", name="WS").workspace_id
    session = SessionService(conn).create_session(workspace_id=workspace_id, session_type="chat")
    channel = ChannelRepository(conn).create(
        workspace_id=workspace_id,
        channel_kind="chat",
        title="secret-chat",
        active_session_id=session.session_id,
    )

    provider = ProviderService(conn).create_provider(
        name="Local OpenAI",
        provider_kind="openai_compatible",
        endpoint_url="http://127.0.0.1:9999/v1",
        api_key_env_var="LOCAL_DUMMY_KEY",
        default_model="local-model",
    )
    profile = ProfileService(conn).create_profile(
        name="Joe Schmo",
        provider=provider.provider_id,
        function="role-assistant",
        model="local-model",
    )
    ParticipantService(conn).add_participant(
        session_id=session.session_id,
        agent_profile_id=profile.agent_profile_id,
    )
    RoutingService(conn).route_message(
        workspace_id=workspace_id,
        channel_id=channel.channel_id,
        source_type="user",
        source_id="Basti",
        target_type="orchestrator",
        target_id="@orchestrator",
        message_kind="conversation",
        session_id=session.session_id,
        payload_ref=json.dumps({"body": "Hi. Wie läufts?"}),
    )

    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["authorization"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"choices": [{"message": {"content": "Alles gut."}}]})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        AgentRuntimeService(conn).generate_for_session(
            session_id=session.session_id,
            user_body="Hi. Wie läufts?",
            user_id="Basti",
        )

    messages = RoutingService(conn).get_messages_by_session(session.session_id)
    assert any("Alles gut." in (m.payload_ref or "") for m in messages)
    assert captured["authorization"] == "Bearer dummy"
    assert captured["body"]["model"] == "local-model"

    conn.close()
