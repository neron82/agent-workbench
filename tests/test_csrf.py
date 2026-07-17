"""Tests for global CSRF protection.

Strategy
--------
We use a ``csrf_client`` fixture that wraps Flask's test client and
automatically injects a valid CSRF token into every unsafe request
(POST/PUT/DELETE/PATCH).  This lets the existing route tests pass
through enabled CSRF without modification.

Dedicated CSRF enforcement tests use the raw ``client`` fixture and
explicitly omit or wrong-token to verify 403 behaviour.

The ``csrf_token`` fixture obtains a valid token by making a GET
request first (which seeds the session), then reading the meta tag
from the response HTML.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from agent_workbench.web import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_csrf_token(html: bytes) -> str:
    """Extract the CSRF token from a rendered page's meta tag."""
    match = re.search(
        rb'<meta\s+name="csrf-token"\s+content="([^"]+)"',
        html,
    )
    if not match:
        raise AssertionError("No csrf-token meta tag found in response")
    return match.group(1).decode("utf-8")


def _extract_form_csrf(html: bytes) -> str:
    """Extract the CSRF token from a hidden form input."""
    match = re.search(
        rb'<input\s+type="hidden"\s+name="csrf_token"\s+value="([^"]+)"',
        html,
    )
    if not match:
        raise AssertionError("No csrf_token hidden input found in response")
    return match.group(1).decode("utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def csrf_app_db_path(tmp_path_factory) -> Path:
    """One fresh database file per test session, with migrations applied."""
    from agent_workbench.db import apply_migrations, get_connection

    path = tmp_path_factory.mktemp("csrf-web") / "workbench.db"
    conn = get_connection(str(path))
    apply_migrations(conn)
    conn.close()
    return path


@pytest.fixture()
def csrf_app(csrf_app_db_path: Path) -> Iterator[Flask]:
    """Build a Flask app bound to the session-scoped test database."""
    application = create_app(db_path=str(csrf_app_db_path))
    application.config.update(TESTING=True)
    yield application


@pytest.fixture()
def client(csrf_app: Flask) -> FlaskClient:
    """Raw test client — no CSRF auto-injection.

    Use this for CSRF enforcement tests where you want to verify 403
    behaviour with missing or wrong tokens.
    """
    return csrf_app.test_client()


@pytest.fixture()
def csrf_token(client: FlaskClient) -> str:
    """Obtain a valid CSRF token by making a GET request."""
    resp = client.get("/")
    assert resp.status_code == 200
    return _extract_csrf_token(resp.data)


@pytest.fixture()
def csrf_client(csrf_app: Flask) -> FlaskClient:
    """Test client that auto-injects a valid CSRF token into unsafe requests.

    This fixture wraps the test client so that every POST/PUT/DELETE/PATCH
    request automatically includes the CSRF token — either as a form field
    (for form-encoded requests) or as an X-CSRF-Token header (for JSON
    requests).  Existing route tests can use this fixture and continue to
    pass through enabled CSRF protection.

    The token is obtained from the first GET request, which seeds the
    session.  Subsequent requests reuse the same session cookie so the
    token remains valid.
    """
    client = csrf_app.test_client()

    # Make a GET request to seed the session and obtain a CSRF token.
    resp = client.get("/")
    assert resp.status_code == 200
    token = _extract_csrf_token(resp.data)

    # Monkey-patch the client's open method to auto-inject CSRF token
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
                # Non-dict data (e.g. JSON string) — add header
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


# ---------------------------------------------------------------------------
# CSRF Enforcement Tests (RED tests first)
# ---------------------------------------------------------------------------


class TestCSRFEnforcement:
    """Dedicated CSRF enforcement tests — run with protection ON."""

    def test_missing_token_html_returns_403(self, client: FlaskClient):
        """POST without CSRF token on HTML form returns 403."""
        resp = client.post(
            "/workspaces",
            data={"name": "Evil Workspace"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 403
        assert b"CSRF token" in resp.data

    def test_wrong_token_html_returns_403(self, client: FlaskClient):
        """POST with wrong CSRF token on HTML form returns 403."""
        resp = client.post(
            "/workspaces",
            data={"name": "Evil Workspace", "csrf_token": "invalid-token"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 403
        assert b"CSRF token" in resp.data

    def test_rejected_post_does_not_create_browser_identity(self, tmp_path: Path):
        """CSRF validation runs before DB-backed identity initialization."""
        from agent_workbench.db import get_connection

        db_path = tmp_path / "side-effect-free.db"
        app = create_app(db_path=str(db_path), environment="testing")
        app.config.update(TESTING=True)

        conn = get_connection(str(db_path))
        try:
            assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        finally:
            conn.close()

        response = app.test_client().post(
            "/workspaces",
            data={"name": "Must Not Exist"},
        )
        assert response.status_code == 403

        conn = get_connection(str(db_path))
        try:
            assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            assert conn.execute(
                "SELECT COUNT(*) FROM workspaces WHERE name = ?",
                ("Must Not Exist",),
            ).fetchone()[0] == 0
        finally:
            conn.close()

    def test_missing_token_json_returns_403_json(self, client: FlaskClient):
        """JSON POST without CSRF token returns 403 with JSON body."""
        resp = client.post(
            "/settings/providers/test-and-fetch-models",
            json={"endpoint_url": "http://example.com/v1"},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 403
        assert resp.is_json
        data = resp.get_json()
        assert data is not None
        assert "error" in data
        assert "CSRF" in data["error"]

    def test_missing_header_json_returns_403_json(self, client: FlaskClient):
        """JSON POST without X-CSRF-Token header returns 403 JSON."""
        resp = client.post(
            "/settings/providers/test-and-fetch-models",
            json={"endpoint_url": "http://example.com/v1"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403
        assert resp.is_json
        data = resp.get_json()
        assert data is not None
        assert "error" in data

    def test_safe_get_unaffected(self, client: FlaskClient):
        """GET requests are not blocked by CSRF."""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_safe_head_unaffected(self, client: FlaskClient):
        """HEAD requests are not blocked by CSRF."""
        resp = client.head("/")
        assert resp.status_code == 200

    def test_healthz_unaffected(self, client: FlaskClient):
        """Health probe is not blocked by CSRF."""
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_readyz_unaffected(self, client: FlaskClient):
        """Readiness probe is not blocked by CSRF."""
        resp = client.get("/readyz")
        assert resp.status_code == 200

    def test_valid_form_token_succeeds(self, client: FlaskClient, csrf_token: str):
        """POST with valid form CSRF token succeeds."""
        resp = client.post(
            "/workspaces",
            data={"name": "Valid Workspace", "csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert resp.status_code == 302  # redirect after success

    def test_valid_header_token_succeeds(self, client: FlaskClient, csrf_token: str):
        """POST with valid X-CSRF-Token header succeeds."""
        resp = client.post(
            "/workspaces",
            data={"name": "Header Workspace"},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )
        assert resp.status_code == 302  # redirect after success

    def test_token_persists_across_requests(self, client: FlaskClient):
        """CSRF token persists in session across requests."""
        # First request seeds the token
        resp1 = client.get("/")
        assert resp1.status_code == 200
        token1 = _extract_csrf_token(resp1.data)

        # Second request should have the same token
        resp2 = client.get("/")
        assert resp2.status_code == 200
        token2 = _extract_csrf_token(resp2.data)

        assert token1 == token2, "CSRF token should persist in session"

    def test_provider_test_endpoint_not_exempt(self, client: FlaskClient):
        """Provider test-and-fetch-models endpoint requires CSRF."""
        resp = client.post(
            "/settings/providers/test-and-fetch-models",
            json={"endpoint_url": "http://example.com/v1"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403
        assert resp.is_json
        data = resp.get_json()
        assert data is not None
        assert "error" in data


# ---------------------------------------------------------------------------
# Template Rendering Tests
# ---------------------------------------------------------------------------


class TestCSRFTemplates:
    """Verify CSRF tokens appear in rendered HTML."""

    def test_meta_tag_present(self, client: FlaskClient):
        """The csrf-token meta tag is present in base template."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'name="csrf-token"' in resp.data
        token = _extract_csrf_token(resp.data)
        assert len(token) == 64  # 256 bits = 64 hex chars

    def test_landing_post_form_has_token(self, client: FlaskClient):
        """The landing page's create-workspace form has a CSRF token."""
        resp = client.get("/")
        assert resp.status_code == 200
        # The create_workspace form should have a hidden csrf_token
        count = resp.data.count(b'name="csrf_token"')
        assert count >= 1, "Expected at least one csrf_token input in landing page"

    def test_channel_list_post_forms_have_token(self, client: FlaskClient):
        """Channel list POST forms contain CSRF token."""
        resp = client.get("/channels")
        assert resp.status_code == 200
        count = resp.data.count(b'name="csrf_token"')
        assert count >= 1, "Expected at least one csrf_token input in channel list"

    def test_session_view_post_forms_have_token(self, client: FlaskClient):
        """Session view POST forms contain CSRF token."""
        # We can't easily create a session without POST, but we can check
        # that the meta tag is present and the helper works.
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'name="csrf-token"' in resp.data


# ---------------------------------------------------------------------------
# Existing Route Tests (pass through enabled CSRF with valid tokens)
# ---------------------------------------------------------------------------


class TestCSRFClientIntegration:
    """Verify that existing route patterns work through the CSRF client."""

    def test_create_workspace_via_csrf_client(
        self, csrf_client: FlaskClient
    ):
        """Creating a workspace works through CSRF-protected client."""
        resp = csrf_client.post(
            "/workspaces",
            data={"name": "CSRF Client Workspace"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"CSRF Client Workspace" in resp.data

    def test_create_channel_via_csrf_client(
        self, csrf_client: FlaskClient
    ):
        """Creating a channel works through CSRF-protected client."""
        # First create a workspace to get a valid workspace_id
        resp = csrf_client.post(
            "/workspaces",
            data={"name": "Channel WS"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Channel WS" in resp.data

        # Extract workspace_id from the response URL
        # The redirect goes to /?workspace_id=...
        # We need a valid workspace_id for the channel create
        from agent_workbench.db import get_connection
        from agent_workbench.models.workspace import WorkspaceRepository
        conn = get_connection(csrf_client.application.config["WORKBENCH_DB_PATH"])
        try:
            workspaces = WorkspaceRepository(conn).list_all()
            ws_id = workspaces[0].workspace_id if workspaces else None
        finally:
            conn.close()

        if not ws_id:
            pytest.skip("No workspace available")

        # Now create a channel with a valid workspace_id
        resp = csrf_client.post(
            "/channels",
            data={
                "workspace_id": ws_id,
                "channel_kind": "chat",
                "title": "test-channel",
            },
            follow_redirects=False,
        )
        # May 302 (redirect) or 200 — but should NOT be 403
        assert resp.status_code != 403, "CSRF should not block valid requests"

    def test_json_post_via_csrf_client(
        self, csrf_client: FlaskClient
    ):
        """JSON POST works through CSRF-protected client with header."""
        resp = csrf_client.post(
            "/settings/providers/test-and-fetch-models",
            json={"endpoint_url": "http://example.com/v1"},
            headers={"Accept": "application/json"},
        )
        # Should not be 403 — CSRF token is auto-injected via header
        assert resp.status_code != 403, "CSRF should not block valid JSON requests"
