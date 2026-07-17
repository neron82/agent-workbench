"""Phase 9 — Operational safeguards tests for the web app.

Covers the hardening/operational-readiness work that lives in
:mod:`agent_workbench.web.app`:

* ``/healthz`` keeps its backwards-compatible text liveness response.
* ``/readyz`` is a new, distinct machine-readable JSON readiness
  probe that exercises the DB connection and the SECRET_KEY.
* ``create_app()`` refuses to start in ``production`` mode when the
  resolved ``SECRET_KEY`` is the well-known development default.
* Production mode enables secure session/cookie defaults; the
  default (testing) mode does *not* over-apply them.

These tests use ``monkeypatch`` to isolate ``WORKBENCH_ENV`` and
``WORKBENCH_SECRET_KEY`` from the surrounding environment so they
are safe to run in any CI configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.web import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_db_path(tmp_path: Path) -> Path:
    """Fresh SQLite file with migrations applied, per-test."""
    path = tmp_path / "workbench.db"
    conn = get_connection(str(path))
    apply_migrations(conn)
    conn.close()
    return path


@pytest.fixture()
def app(
    app_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Flask]:
    """Default ``create_app`` call (testing mode, dev secret).

    Mirrors the historical call shape used elsewhere in the suite
    and confirms backwards compatibility for callers that don't
    pass ``environment=``. ``WORKBENCH_ENV`` / ``WORKBENCH_SECRET_KEY``
    are cleared so the test is independent of the shell environment.
    """
    monkeypatch.delenv("WORKBENCH_ENV", raising=False)
    monkeypatch.delenv("WORKBENCH_SECRET_KEY", raising=False)
    application = create_app(db_path=str(app_db_path))
    application.config.update(TESTING=True)
    yield application


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    from tests.conftest import make_csrf_client
    return make_csrf_client(app)


def _build_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    environment: str | None = None,
    secret_key: str | None = None,
) -> Flask:
    """Build an app with the given env + secret, isolated from the shell.

    Both ``WORKBENCH_ENV`` and ``WORKBENCH_SECRET_KEY`` are reset so
    the only inputs the factory sees are the values passed in.
    """
    monkeypatch.delenv("WORKBENCH_ENV", raising=False)
    monkeypatch.delenv("WORKBENCH_SECRET_KEY", raising=False)
    if environment is not None:
        monkeypatch.setenv("WORKBENCH_ENV", environment)
    if secret_key is not None:
        monkeypatch.setenv("WORKBENCH_SECRET_KEY", secret_key)
    return create_app(db_path=str(tmp_path / "workbench.db"))


# ---------------------------------------------------------------------------
# /healthz — backwards compatibility
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_healthz_returns_ok_text(self, client: FlaskClient):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert b"ok=1" in resp.data

    def test_healthz_is_cheap_text_not_json(self, client: FlaskClient):
        """The legacy liveness endpoint stays as a text response.

        ``/readyz`` is the new JSON surface; ``/healthz`` must not
        silently start returning JSON or operators' existing probes
        will keep working but JSON-aware tooling will be confused.
        """
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert not resp.is_json
        assert b"=" in resp.data


# ---------------------------------------------------------------------------
# /readyz — readiness probe
# ---------------------------------------------------------------------------


class TestReadyz:
    def test_readyz_returns_ok_json_in_default_mode(self, client: FlaskClient):
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.get_json()
        assert isinstance(body, dict)
        assert body["ok"] is True
        assert body["db_ok"] is True
        assert body["secret_key_ok"] is True
        assert body["using_dev_secret"] is True
        assert body["environment"] == "testing"
        # All required keys are present.
        for key in ("ok", "db_ok", "secret_key_ok", "environment", "using_dev_secret"):
            assert key in body

    def test_readyz_in_development_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        application = _build_app(tmp_path, monkeypatch, environment="development")
        client = application.test_client()
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["environment"] == "development"
        assert body["db_ok"] is True
        # In dev, the dev secret is acceptable (the refusal only fires
        # in production mode at factory time). /readyz still reports it
        # honestly so dashboards can show "using dev secret".
        assert body["using_dev_secret"] is True
        assert body["secret_key_ok"] is True

    def test_readyz_in_production_with_strong_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        application = _build_app(
            tmp_path,
            monkeypatch,
            environment="production",
            secret_key="a-strong-random-secret-not-the-default",
        )
        client = application.test_client()
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["db_ok"] is True
        assert body["secret_key_ok"] is True
        assert body["using_dev_secret"] is False
        assert body["environment"] == "production"

    def test_readyz_in_production_with_dev_secret_is_not_reachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The factory refuses production + dev secret, so /readyz never
        gets to *report* that case — but if a future change ever lets
        such an app boot (e.g. via a hot reload that replaces the
        key with the dev value), the probe must still report the
        failure so an orchestrator can take the instance out of
        rotation. We simulate that hot-reload scenario by mutating
        ``SECRET_KEY`` post-construction.
        """
        application = _build_app(
            tmp_path,
            monkeypatch,
            environment="production",
            secret_key="a-strong-random-secret-not-the-default",
        )
        # Simulate a runtime SECRET_KEY replacement with the dev value
        # (e.g. someone pushed a config change).
        application.config["SECRET_KEY"] = "workbench-dev-secret"
        client = application.test_client()
        resp = client.get("/readyz")
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["ok"] is False
        assert body["db_ok"] is True
        assert body["secret_key_ok"] is False
        assert body["using_dev_secret"] is True
        assert body["environment"] == "production"

    def test_readyz_returns_non_200_when_db_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A failing DB must make /readyz return 503, not 200.

        We simulate a failing DB by injecting a *closed* connection
        via ``WORKBENCH_DB_CONN``. The before-request hook honours
        the injected connection (and skips its teardown), so the
        first ``SELECT 1`` inside ``/readyz`` raises a
        ``sqlite3.ProgrammingError`` which the probe must catch and
        surface as ``db_ok=False`` + HTTP 503.
        """
        monkeypatch.delenv("WORKBENCH_ENV", raising=False)
        monkeypatch.delenv("WORKBENCH_SECRET_KEY", raising=False)
        db_path = tmp_path / "workbench.db"
        broken = get_connection(str(db_path))
        apply_migrations(broken)
        broken.close()  # any subsequent query raises
        application = create_app(db_path=str(db_path))
        application.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        # Inject the closed connection so the before-request hook
        # uses it instead of opening a fresh one.
        application.config["WORKBENCH_DB_CONN"] = broken
        client = application.test_client()
        resp = client.get("/readyz")
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["ok"] is False
        assert body["db_ok"] is False
        # The dev secret is acceptable in testing mode, so
        # ``secret_key_ok`` stays True — only ``db_ok`` failed.
        assert body["secret_key_ok"] is True
        assert body["environment"] == "testing"

    def test_readyz_response_is_machine_readable_json(self, client: FlaskClient):
        resp = client.get("/readyz")
        # Flask test client's ``is_json`` checks the content-type too.
        assert resp.is_json
        # Round-trips through ``json.loads`` to catch any non-JSON bytes
        # in the body (e.g. leading whitespace or HTML error pages).
        parsed = json.loads(resp.get_data(as_text=True))
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# create_app() — production refuses the dev secret
# ---------------------------------------------------------------------------


class TestCreateAppProductionGuard:
    def test_production_mode_with_default_dev_secret_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Production + dev default SECRET_KEY is refused at factory time."""
        monkeypatch.delenv("WORKBENCH_ENV", raising=False)
        monkeypatch.delenv("WORKBENCH_SECRET_KEY", raising=False)
        monkeypatch.setenv("WORKBENCH_ENV", "production")
        # No WORKBENCH_SECRET_KEY -> falls through to the dev default.
        with pytest.raises(RuntimeError) as exc:
            create_app(db_path=str(tmp_path / "workbench.db"))
        assert "SECRET_KEY" in str(exc.value)

    def test_production_mode_with_strong_secret_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("WORKBENCH_ENV", raising=False)
        monkeypatch.delenv("WORKBENCH_SECRET_KEY", raising=False)
        monkeypatch.setenv("WORKBENCH_ENV", "production")
        monkeypatch.setenv("WORKBENCH_SECRET_KEY", "a-strong-random-secret")
        app = create_app(db_path=str(tmp_path / "workbench.db"))
        assert app.config["WORKBENCH_ENV"] == "production"
        assert app.config["SECRET_KEY"] == "a-strong-random-secret"

    def test_development_mode_with_dev_secret_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Dev mode keeps low-friction behaviour — no refusal."""
        monkeypatch.delenv("WORKBENCH_ENV", raising=False)
        monkeypatch.delenv("WORKBENCH_SECRET_KEY", raising=False)
        monkeypatch.setenv("WORKBENCH_ENV", "development")
        app = create_app(db_path=str(tmp_path / "workbench.db"))
        assert app.config["WORKBENCH_ENV"] == "development"

    def test_testing_mode_with_dev_secret_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Default (testing) mode keeps the dev secret — no refusal."""
        monkeypatch.delenv("WORKBENCH_ENV", raising=False)
        monkeypatch.delenv("WORKBENCH_SECRET_KEY", raising=False)
        # No WORKBENCH_ENV set -> defaults to testing.
        app = create_app(db_path=str(tmp_path / "workbench.db"))
        assert app.config["WORKBENCH_ENV"] == "testing"

    def test_unknown_environment_raises_value_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("WORKBENCH_ENV", raising=False)
        monkeypatch.setenv("WORKBENCH_ENV", "staging-not-a-real-env")
        with pytest.raises(ValueError):
            create_app(db_path=str(tmp_path / "workbench.db"))

    def test_explicit_environment_argument_overrides_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The explicit ``environment=`` argument wins over the env var.

        We set the env var to ``development`` and pass
        ``environment="production"`` with a strong secret, then
        confirm the resulting app is in production mode with the
        secure cookie defaults applied.
        """
        monkeypatch.delenv("WORKBENCH_ENV", raising=False)
        monkeypatch.delenv("WORKBENCH_SECRET_KEY", raising=False)
        monkeypatch.setenv("WORKBENCH_ENV", "development")
        monkeypatch.setenv("WORKBENCH_SECRET_KEY", "a-strong-random-secret")
        app = create_app(
            db_path=str(tmp_path / "workbench.db"),
            environment="production",
        )
        assert app.config["WORKBENCH_ENV"] == "production"
        assert app.config["SESSION_COOKIE_SECURE"] is True


# ---------------------------------------------------------------------------
# Cookie / session defaults
# ---------------------------------------------------------------------------


class TestCookieDefaults:
    def test_production_enables_secure_cookie_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        application = _build_app(
            tmp_path,
            monkeypatch,
            environment="production",
            secret_key="a-strong-random-secret",
        )
        # All five secure defaults must be on in production.
        assert application.config["SESSION_COOKIE_SECURE"] is True
        assert application.config["SESSION_COOKIE_HTTPONLY"] is True
        assert application.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert application.config["REMEMBER_COOKIE_SECURE"] is True
        assert application.config["REMEMBER_COOKIE_HTTPONLY"] is True

    def test_testing_mode_does_not_over_apply_secure_defaults(self, app: Flask):
        """Default (testing) mode must NOT flip secure cookies on.

        Secure cookies over plain-HTTP test clients break the
        ``test_client`` cookie jar in non-obvious ways. The
        safeguard only kicks in for explicit production mode.
        """
        # SESSION_COOKIE_SECURE defaults to False on a plain Flask app
        # and the factory must not flip it on in test/dev mode.
        assert app.config["SESSION_COOKIE_SECURE"] is False
        # SESSION_COOKIE_SAMESITE is set to 'Lax' in all environments
        # for CSRF protection; HttpOnly is also set globally.
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_development_mode_does_not_over_apply_secure_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        application = _build_app(tmp_path, monkeypatch, environment="development")
        assert application.config["SESSION_COOKIE_SECURE"] is False
        assert application.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert application.config["SESSION_COOKIE_HTTPONLY"] is True
