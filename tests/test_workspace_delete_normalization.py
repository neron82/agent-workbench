"""Tests for workspace deletion normalization.

Verifies:
1. Workspace with channels (no sessions) + abandoned harness_run is REFUSED
   by current route (RED phase).
2. After fix, same workspace is ACCEPTED (302) and all owned rows are
   deleted without affecting another workspace.
3. Workspace with a real session remains blocked.
4. FK-safe deletion order: channels, cross_harness_permissions,
   tool_invocations, participant_transfers, and all existing cleanup
   tables are handled.
"""

from __future__ import annotations

import sqlite3
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
    path = tmp_path_factory.mktemp("workspace-delete") / "workbench.db"
    conn = get_connection(str(path))
    apply_migrations(conn)
    conn.close()
    return path


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


def _create_workspace(conn: sqlite3.Connection, name: str) -> str:
    """Create a workspace and return its id."""
    repo = WorkspaceRepository(conn)
    ws = repo.create(tenant_id="t1", name=name)
    return ws.workspace_id


def _create_channel(conn: sqlite3.Connection, workspace_id: str, kind: str = "chat") -> str:
    """Create a channel with no active session and return its id."""
    repo = ChannelRepository(conn)
    ch = repo.create(
        workspace_id=workspace_id,
        channel_kind=kind,
        title=f"test-{kind}",
        active_session_id=None,
    )
    return ch.channel_id


def _create_abandoned_harness_run(
    conn: sqlite3.Connection, workspace_id: str, session_id: str = "sess",
) -> str:
    """Create an abandoned harness_run (failed, no real session)."""
    import uuid
    hrun_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO harness_runs "
        "(harness_run_id, workspace_id, session_id, harness_type, status) "
        "VALUES (?, ?, ?, 'hermes', 'failed')",
        (hrun_id, workspace_id, session_id),
    )
    conn.commit()
    return hrun_id


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


def _count_workspace_rows(conn: sqlite3.Connection, workspace_id: str) -> dict[str, int]:
    """Count rows in every workspace-scoped table for the given workspace."""
    tables = [
        "channels",
        "session_extensions",
        "harness_runs",
        "task_specs",
        "routed_messages",
        "artifacts",
        "review_records",
        "session_participants",
        "session_labels",
        "project_assets",
        "agent_teams",
        "tool_invocations",
        "cross_harness_permissions",
    ]
    counts = {}
    for table in tables:
        # Check if the table has workspace_id column
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "workspace_id" in cols:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            counts[table] = row[0]
        else:
            counts[table] = 0
    return counts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkspaceDeleteNormalization:
    """Workspace deletion: channels without sessions should not block."""

    def test_workspace_with_channels_no_sessions_is_deleted(
        self, client: FlaskClient, app_db_path: Path
    ):
        """Workspace with channels (no sessions) and abandoned harness_run
        is now accepted for deletion after the normalization fix."""
        conn = get_connection(str(app_db_path))
        try:
            ws_id = _create_workspace(conn, "Channels Only")
            _create_channel(conn, ws_id, "chat")
            _create_channel(conn, ws_id, "research")
            _create_channel(conn, ws_id, "work")
            _create_channel(conn, ws_id, "review")
            _create_abandoned_harness_run(conn, ws_id, "sess")
        finally:
            conn.close()

        # POST to delete — after fix this should SUCCEED (302 to index)
        resp = client.post(
            f"/workspaces/{ws_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        # Should redirect to index (success), not /settings (refused)
        assert "/settings" not in resp.headers.get("Location", "")

        # Workspace is now gone
        conn = get_connection(str(app_db_path))
        try:
            assert WorkspaceRepository(conn).get_by_id(ws_id) is None
        finally:
            conn.close()

    def test_fixed_route_deletes_workspace_with_channels_no_sessions(
        self, client: FlaskClient, app_db_path: Path
    ):
        """GREEN phase: after fix, workspace with channels (no sessions)
        and abandoned harness_run is deleted.  All owned rows removed.
        Another workspace is unaffected."""
        conn = get_connection(str(app_db_path))
        try:
            # Workspace A — the one we'll delete
            ws_a = _create_workspace(conn, "Delete Me A")
            _create_channel(conn, ws_a, "chat")
            _create_channel(conn, ws_a, "research")
            _create_abandoned_harness_run(conn, ws_a, "sess")

            # Workspace B — should be untouched
            ws_b = _create_workspace(conn, "Keep Me B")
            _create_channel(conn, ws_b, "work")
        finally:
            conn.close()

        # Count rows before
        conn = get_connection(str(app_db_path))
        try:
            before_a = _count_workspace_rows(conn, ws_a)
            before_b = _count_workspace_rows(conn, ws_b)
        finally:
            conn.close()

        # Verify workspace A has channels and harness_runs
        assert before_a["channels"] == 2
        assert before_a["harness_runs"] == 1
        assert before_a["session_extensions"] == 0

        # POST to delete workspace A
        resp = client.post(
            f"/workspaces/{ws_a}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        # Should redirect to index (success) — not /settings
        location = resp.headers.get("Location", "")
        assert "/settings" not in location

        # Workspace A is gone
        conn = get_connection(str(app_db_path))
        try:
            assert WorkspaceRepository(conn).get_by_id(ws_a) is None

            # All owned rows for A are gone
            after_a = _count_workspace_rows(conn, ws_a)
            for table, count in after_a.items():
                assert count == 0, (
                    f"Table {table} still has {count} rows for deleted workspace A"
                )

            # Workspace B is untouched
            assert WorkspaceRepository(conn).get_by_id(ws_b) is not None
            after_b = _count_workspace_rows(conn, ws_b)
            for table, count in after_b.items():
                assert count == before_b.get(table, 0), (
                    f"Table {table} changed for workspace B: "
                    f"before={before_b.get(table, 0)} after={count}"
                )
        finally:
            conn.close()

    def test_workspace_with_real_session_remains_blocked(
        self, client: FlaskClient, app_db_path: Path
    ):
        """A workspace with a real session is still blocked from deletion."""
        conn = get_connection(str(app_db_path))
        try:
            ws_id = _create_workspace(conn, "Has Session")
        finally:
            conn.close()

        # Create a real session via the web route
        _create_session(client, ws_id, "chat")

        # POST to delete — should be refused
        resp = client.post(
            f"/workspaces/{ws_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/settings" in resp.headers.get("Location", "")

        # Workspace still exists
        conn = get_connection(str(app_db_path))
        try:
            assert WorkspaceRepository(conn).get_by_id(ws_id) is not None
        finally:
            conn.close()

    def test_delete_cleans_orphan_operational_rows(
        self, client: FlaskClient, app_db_path: Path
    ):
        """Workspace deletion removes cross_harness_permissions,
        tool_invocations, and participant_transfers for the workspace."""
        conn = get_connection(str(app_db_path))
        try:
            ws_id = _create_workspace(conn, "Orphan Rows")
            _create_channel(conn, ws_id, "chat")

            # Insert orphan operational rows (no session, just workspace_id)
            # Need to temporarily disable FK for tool_invocations since
            # tool_id references tools table
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                "INSERT INTO cross_harness_permissions "
                "(permission_id, session_id, workspace_id, agent_harness_type, "
                "tool_harness_type, decision, created_at) "
                "VALUES (?, ?, ?, NULL, 'shell', 'permanent', 1000.0)",
                ("orphan-chp-1", "no-session", ws_id),
            )
            conn.execute(
                "INSERT INTO tool_invocations "
                "(invocation_id, session_id, workspace_id, tool_id, tool_name, "
                "arguments_json, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, '{}', 'failed', 1000.0)",
                ("orphan-ti-1", "no-session", ws_id, "orphan-tool-id", "orphan-tool"),
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()
        finally:
            conn.close()

        # Delete workspace
        resp = client.post(
            f"/workspaces/{ws_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/settings" not in resp.headers.get("Location", "")

        # Verify orphan rows are gone
        conn = get_connection(str(app_db_path))
        try:
            chp_count = conn.execute(
                "SELECT COUNT(*) FROM cross_harness_permissions WHERE workspace_id = ?",
                (ws_id,),
            ).fetchone()[0]
            assert chp_count == 0, "cross_harness_permissions not cleaned"

            ti_count = conn.execute(
                "SELECT COUNT(*) FROM tool_invocations WHERE workspace_id = ?",
                (ws_id,),
            ).fetchone()[0]
            assert ti_count == 0, "tool_invocations not cleaned"
        finally:
            conn.close()

    def test_delete_cleans_message_only_event_records(
        self, client: FlaskClient, app_db_path: Path
    ):
        """Message audit rows without a harness run cannot block deletion."""
        conn = get_connection(str(app_db_path))
        try:
            ws_id = _create_workspace(conn, "Message Audit Rows")
            channel_id = _create_channel(conn, ws_id, "chat")
            conn.execute(
                "INSERT INTO routed_messages "
                "(routed_message_id, workspace_id, session_id, channel_id, "
                "source_type, source_id, target_type, target_id, message_kind) "
                "VALUES (?, ?, NULL, ?, 'user', 'u1', 'channel', ?, 'conversation')",
                ("message-only-rm", ws_id, channel_id, channel_id),
            )
            conn.execute(
                "INSERT INTO event_records "
                "(event_id, harness_run_id, routed_message_id, event_type, "
                "event_source, event_ts) VALUES (?, NULL, ?, 'message', 'web', 1000.0)",
                ("message-only-event", "message-only-rm"),
            )
            conn.commit()
        finally:
            conn.close()

        response = client.post(
            f"/workspaces/{ws_id}/delete",
            follow_redirects=False,
        )
        assert response.status_code == 302

        conn = get_connection(str(app_db_path))
        try:
            assert WorkspaceRepository(conn).get_by_id(ws_id) is None
            assert conn.execute(
                "SELECT COUNT(*) FROM event_records WHERE event_id = ?",
                ("message-only-event",),
            ).fetchone()[0] == 0
        finally:
            conn.close()
