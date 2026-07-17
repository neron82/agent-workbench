from __future__ import annotations

import re

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.agent_status import AgentStatusTracker
from agent_workbench.web import create_app


def _csrf_client(app, db_path: str):
    """Return a test client with a valid CSRF token seeded in the session."""
    client = app.test_client()
    # Seed the session with a CSRF token
    resp = client.get("/")
    assert resp.status_code == 200
    match = re.search(
        rb'<meta\s+name="csrf-token"\s+content="([^"]+)"',
        resp.data,
    )
    assert match, "No csrf-token meta tag found"
    token = match.group(1).decode("utf-8")

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
                if isinstance(data, dict):
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


def test_stop_all_agents_stops_every_requested_agent_without_redirect(tmp_path):
    db_path = tmp_path / "status-route.db"
    app = create_app(db_path=str(db_path))
    app.config.update(TESTING=True)

    conn = get_connection(str(db_path))
    apply_migrations(conn)
    workspace = WorkspaceRepository(conn).create(tenant_id="default", name="Status")
    session = SessionExtensionRepository(conn).create(
        workspace_id=workspace.workspace_id,
        session_type="chat",
        title="Status test",
    )
    conn.close()

    tracker = AgentStatusTracker.get_instance()
    tracker.start_agent(session.session_id, "Alpha")
    tracker.start_agent(session.session_id, "Beta")
    try:
        response = _csrf_client(app, str(db_path)).post(
            f"/sessions/{session.session_id}/stop-all-agents",
            json={"agent_names": ["Alpha", "Beta"]},
        )
        assert response.status_code == 200
        assert response.get_json() == {
            "stopped": ["Alpha", "Beta"],
            "inactive": [],
        }
        assert tracker.get_status(session.session_id, "Alpha").status == "stopped"
        assert tracker.get_status(session.session_id, "Beta").status == "stopped"
    finally:
        tracker.cleanup_session(session.session_id)


def test_transfer_route_creates_continuation_session(tmp_path):
    db_path = tmp_path / "transfer-route.db"
    app = create_app(db_path=str(db_path))
    app.config.update(TESTING=True)

    conn = get_connection(str(db_path))
    apply_migrations(conn)
    workspace = WorkspaceRepository(conn).create(tenant_id="default", name="Transfer")
    session = SessionExtensionRepository(conn).create(
        workspace_id=workspace.workspace_id,
        session_type="chat",
        title="Original",
    )
    conn.close()

    response = _csrf_client(app, str(db_path)).post(
        f"/sessions/{session.session_id}/transfer",
        data={
            "title": "Continuation",
            "session_type": "chat",
            "context_summary": "Carry this forward",
        },
    )
    assert response.status_code == 302
    assert "/sessions/" in response.headers["Location"]


def test_post_message_repairs_missing_session_channel(tmp_path):
    db_path = tmp_path / "message-channel-repair.db"
    app = create_app(db_path=str(db_path))
    app.config.update(TESTING=True)

    conn = get_connection(str(db_path))
    apply_migrations(conn)
    workspace = WorkspaceRepository(conn).create(tenant_id="default", name="Messages")
    session = SessionExtensionRepository(conn).create(
        workspace_id=workspace.workspace_id,
        session_type="research",
        title="Orphan",
    )
    conn.close()

    response = _csrf_client(app, str(db_path)).post(
        f"/sessions/{session.session_id}/message",
        data={"body": "hello"},
    )
    assert response.status_code == 302

    conn = get_connection(str(db_path))
    try:
        channel = conn.execute(
            "SELECT channel_kind, active_session_id FROM channels "
            "WHERE active_session_id = ?",
            (session.session_id,),
        ).fetchone()
        assert channel is not None
        assert channel["channel_kind"] == "research"
    finally:
        conn.close()
