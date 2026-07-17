"""Tests for the /settings/tools UI route."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.tool import ToolRepository
from agent_workbench.web.app import create_app


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "tools-ui.db"
    conn = get_connection(str(path))
    apply_migrations(conn)
    from agent_workbench.services.tool_seeds import seed_builtin_tools
    seed_builtin_tools(conn)
    conn.close()
    return path


@pytest.fixture()
def app(db_path: Path) -> Iterator[Flask]:
    application = create_app(db_path=str(db_path))
    application.config.update(TESTING=True)
    yield application


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    from tests.conftest import make_csrf_client
    return make_csrf_client(app)


def test_tools_page_lists_builtin_tools(client):
    """The Tools page must render the seeded builtin tools."""
    resp = client.get("/settings/tools")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    for name in ("run_command", "write_file", "delegate_subagent"):
        assert name in body
    assert 'data-testid="tools-table"' in body


def test_tools_page_lists_disabled_tool(client, db_path):
    """Disabled tools still show up so the user can re-enable them."""
    conn = get_connection(str(db_path))
    try:
        repo = ToolRepository(conn)
        for t in repo.list_enabled():
            if t.name == "run_command" and t.harness_type == "shell":
                repo.update(t.tool_id, is_enabled=False)
                break
    finally:
        conn.close()
    resp = client.get("/settings/tools")
    body = resp.data.decode("utf-8")
    assert resp.status_code == 200
    assert "inaktiv" in body


def test_tool_toggle_flips_enabled_flag(client, db_path):
    """Toggling a tool flips its is_enabled and re-renders the page."""
    conn = get_connection(str(db_path))
    try:
        repo = ToolRepository(conn)
        # Toggle a genuinely enabled tool. ``delegate_subagent`` is kept in
        # the catalog for visibility but is disabled until it has a real
        # implementation.
        t = next(iter(repo.list_enabled()), None)
        assert t is not None
        assert t.is_enabled is True
    finally:
        conn.close()

    resp = client.post(f"/settings/tools/{t.tool_id}/toggle", follow_redirects=False)
    assert resp.status_code == 302

    conn = get_connection(str(db_path))
    try:
        again = ToolRepository(conn).get_by_id(t.tool_id)
        assert again is not None
        assert again.is_enabled is False
    finally:
        conn.close()

    client.post(f"/settings/tools/{t.tool_id}/toggle", follow_redirects=False)
    conn = get_connection(str(db_path))
    try:
        again2 = ToolRepository(conn).get_by_id(t.tool_id)
        assert again2 is not None
        assert again2.is_enabled is True
    finally:
        conn.close()


def test_tool_toggle_unknown_id_returns_404(client):
    resp = client.post("/settings/tools/does-not-exist/toggle")
    assert resp.status_code == 404
