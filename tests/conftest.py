"""Shared test fixtures for Agent Workbench."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from agent_workbench.db import get_connection, apply_migrations


def make_csrf_client(app) -> FlaskClient:
    """Return a test client with a valid CSRF token auto-injected.

    Makes a GET request first to seed the session with a CSRF token,
    then monkey-patches ``client.open`` to inject the token into every
    unsafe request (POST/PUT/DELETE/PATCH).

    Use this in any test that creates its own ``app.test_client()``
    so existing route tests pass through enabled CSRF protection.
    """
    client = app.test_client()

    # Seed the session with a CSRF token via a GET request.
    resp = client.get("/")
    assert resp.status_code == 200
    match = re.search(
        rb'<meta\s+name="csrf-token"\s+content="([^"]+)"',
        resp.data,
    )
    assert match, "No csrf-token meta tag found in response"
    token = match.group(1).decode("utf-8")

    original_open = client.open

    def _patched_open(*args, **kwargs):
        method = kwargs.get("method", "GET")
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            data = kwargs.get("data")
            json_data = kwargs.get("json")
            headers = dict(kwargs.get("headers", {}))

            if json_data is not None:
                # JSON request: add X-CSRF-Token header
                headers.setdefault("X-CSRF-Token", token)
                kwargs["headers"] = headers
            elif data is not None and isinstance(data, dict):
                # Form-encoded or multipart: add csrf_token to data
                data = dict(data)
                data.setdefault("csrf_token", token)
                kwargs["data"] = data
            elif data is not None and not isinstance(data, dict):
                # Non-dict data (e.g. JSON string with content_type) — add header
                headers.setdefault("X-CSRF-Token", token)
                kwargs["headers"] = headers
            else:
                # No data yet — add csrf_token as form field
                kwargs["data"] = {"csrf_token": token}
                headers.setdefault("X-CSRF-Token", token)
                kwargs["headers"] = headers
        return original_open(*args, **kwargs)

    client.open = _patched_open  # type: ignore[method-assign]
    return client


@pytest.fixture
def tmp_db(tmp_path: Path):
    """Return a path to a temporary SQLite database file."""
    return tmp_path / "test.db"


@pytest.fixture
def db(tmp_db: Path):
    """Return a connection to a freshly created, migrated database."""
    conn = get_connection(str(tmp_db))
    apply_migrations(conn)
    # Seed builtin tools so tests that exercise the tool registry or
    # dispatcher don't need to do it themselves.
    from agent_workbench.services.tool_seeds import seed_builtin_tools
    seed_builtin_tools(conn)
    yield conn
    conn.close()
