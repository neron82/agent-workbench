"""Flask app factory for the Agent Workbench web UI.

The factory wires the three blueprints (``channels``, ``sessions``,
``messages``), opens a per-request SQLite connection to ``workbench.db``,
and registers the base template that drives the global nav bar.

The web layer never opens long-lived database connections: each request
opens a connection via :func:`agent_workbench.db.get_connection` and
ensures the schema is migrated. The connection is stored on Flask's
``g`` object so service-layer instances can be constructed from it.

Operational safeguards (Phase 9):
    * ``/healthz`` is a liveness probe and stays cheap.
    * ``/readyz`` is a separate readiness probe that exercises the
      database and verifies the Flask ``SECRET_KEY`` is not the
      well-known development default. It returns machine-readable
      JSON and a non-200 status when any check fails.
    * Environment is env-driven via ``WORKBENCH_ENV`` (values:
      ``production``, ``development``, ``testing``). The factory
      refuses to start in ``production`` when the configured
      ``SECRET_KEY`` is the well-known development default. In
      ``production`` we also enable secure session/cookie defaults.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from flask import Flask, g, jsonify, render_template

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.services.agent_runtime_service import extract_message_body
from agent_workbench.services.orchestrator_service import OrchestratorService
from agent_workbench.services.participant_service import ParticipantService
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.provider_service import ProviderService
from agent_workbench.services.role_service import RoleService
from agent_workbench.services.routing_service import RoutingService
from agent_workbench.services.session_service import SessionService


# Default DB path: ``workbench.db`` at the project root, matching the
# default used by :func:`agent_workbench.db.get_connection`.
_DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "workbench.db"


# Well-known development SECRET_KEY used as the default when neither
# ``WORKBENCH_SECRET_KEY`` nor an explicit override is provided. We
# refuse to start in production mode with this value because Flask
# session cookies signed with a known key are trivially forgeable.
_DEV_SECRET_KEY = "workbench-dev-secret"

# Set of SECRET_KEY values that are considered inherently insecure for
# production deployments. Keep this small and explicit; only the values
# we ship as defaults belong here.
_INSECURE_PRODUCTION_SECRETS = frozenset({_DEV_SECRET_KEY})

# Valid WORKBENCH_ENV values.
_ALLOWED_ENVIRONMENTS = ("production", "development", "testing")


def _resolve_environment(explicit: Optional[str] = None) -> str:
    """Return the resolved environment name.

    The lookup order is: ``explicit`` argument, ``WORKBENCH_ENV`` env
    var, fallback to ``"testing"`` (lowest-friction default so the
    factory never refuses a developer/CI invocation by accident).
    Unknown values raise ``ValueError`` so typos surface immediately.
    """
    raw = explicit if explicit is not None else os.environ.get("WORKBENCH_ENV")
    env = (raw or "testing").strip().lower()
    if env not in _ALLOWED_ENVIRONMENTS:
        raise ValueError(
            f"WORKBENCH_ENV={raw!r} is not one of {_ALLOWED_ENVIRONMENTS!r}"
        )
    return env


def _resolve_secret_key(environment: str) -> str:
    """Return the configured ``SECRET_KEY`` for ``environment``.

    The key is taken from ``WORKBENCH_SECRET_KEY`` when set, otherwise
    the well-known development default is used. The caller is
    responsible for refusing the default in ``production``.
    """
    return os.environ.get("WORKBENCH_SECRET_KEY") or _DEV_SECRET_KEY


def _apply_production_cookie_defaults(app: Flask) -> None:
    """Enable secure cookie / session defaults for production mode.

    These values are only applied when ``environment == "production"``;
    tests and development runs keep Flask's defaults so cookies stay
    easy to use over plain HTTP and inside the test client.

    We use direct assignment (not :meth:`dict.setdefault`) because
    Flask's ``Config`` pre-populates keys like
    ``SESSION_COOKIE_SECURE = False`` and
    ``SESSION_COOKIE_SAMESITE = None`` at app construction time, so
    ``setdefault`` would silently preserve the insecure defaults.
    """
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SECURE"] = True
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True


def create_app(
    db_path: Optional[str | Path] = None,
    environment: Optional[str] = None,
) -> Flask:
    """Build and return a configured Flask app.

    Parameters
    ----------
    db_path:
        Optional path to the SQLite database file. When ``None`` the
        application default (``workbench.db`` next to the project root)
        is used. Tests typically pass a ``tmp_path``-based path.
    environment:
        Optional explicit environment name. When ``None`` the
        ``WORKBENCH_ENV`` env var is consulted; when that is also
        unset, ``"testing"`` is used. Must be one of
        ``"production"``, ``"development"``, ``"testing"``.

    Returns
    -------
    Flask
        A Flask application with the ``channels``, ``sessions``, and
        ``messages`` blueprints registered and a per-request database
        connection lifecycle.

    Raises
    ------
    RuntimeError
        If ``environment == "production"`` and the resolved
        ``SECRET_KEY`` is one of the values in
        :data:`_INSECURE_PRODUCTION_SECRETS`. We fail fast at factory
        time rather than discovering the issue via a forged session
        cookie in production.
    ValueError
        If ``environment`` is not one of the allowed values.
    """
    env = _resolve_environment(environment)
    secret_key = _resolve_secret_key(env)

    if env == "production" and secret_key in _INSECURE_PRODUCTION_SECRETS:
        raise RuntimeError(
            "Refusing to start in production mode with the development "
            "default SECRET_KEY. Set WORKBENCH_SECRET_KEY to a strong "
            "random value before deploying."
        )

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
    )

    # SECRET_KEY is required for ``flash()`` (session-backed message queue).
    # In non-production environments we default to the dev value to keep
    # tests / dev low-friction; production mode is checked above and
    # raises before we get here if the default leaked through.
    app.config["SECRET_KEY"] = secret_key
    app.config["WORKBENCH_ENV"] = env

    # Stash the resolved DB path on the app so the before-request hook
    # can find it without callers having to re-pass it.
    app.config["WORKBENCH_DB_PATH"] = (
        str(db_path) if db_path is not None else str(_DEFAULT_DB_PATH)
    )
    app.config["WORKBENCH_TESTING"] = False
    app.config["WORKBENCH_AGENT_RESPONSE_MODE"] = os.environ.get(
        "WORKBENCH_AGENT_RESPONSE_MODE", "async"
    )

    # In production, lock down cookie / session defaults so that
    # ``flash()`` and any future server-side session use cannot be
    # downgraded by a misconfigured reverse proxy. Tests and dev
    # intentionally keep Flask's lax defaults.
    if env == "production":
        _apply_production_cookie_defaults(app)

    # Seed builtin tools once at factory time (idempotent).
    # Open a temporary connection for the seed; the per-request hook
    # opens its own connection later.
    from agent_workbench.services.tool_seeds import seed_builtin_tools
    try:
        seed_conn = get_connection(app.config["WORKBENCH_DB_PATH"])
        apply_migrations(seed_conn)
        seed_builtin_tools(seed_conn)
        seed_conn.close()
    except Exception:  # pragma: no cover - defensive
        app.logger.exception("Failed to seed builtin tools")

    @app.before_request
    def _open_db_connection() -> None:
        """Open (and migrate) a per-request DB connection.

        The connection is stored on :data:`flask.g` so blueprints and
        templates can reach it via :func:`get_db`.
        """
        if "db" in g:
            return
        # Allow tests to inject a pre-existing connection
        shared = app.config.get("WORKBENCH_DB_CONN")
        if shared is not None:
            g.db = shared
            return
        conn = get_connection(app.config["WORKBENCH_DB_PATH"])
        apply_migrations(conn)
        g.db = conn

    @app.teardown_request
    def _close_db_connection(_exc: Optional[BaseException]) -> None:
        # Don't close injected test connections
        if app.config.get("WORKBENCH_DB_CONN") is not None:
            g.pop("db", None)
            return
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.route("/healthz")
    def healthz() -> str:
        """Liveness probe that exercises the DB connection.

        Kept as a cheap text response for backwards compatibility with
        existing probes / smoke tests. Readiness checks (including
        SECRET_KEY validation) live on ``/readyz``.
        """
        conn = get_db()
        row = conn.execute("SELECT 1 AS ok").fetchone()
        return f"ok={row['ok']}"

    @app.route("/readyz")
    def readyz():
        """Readiness probe — machine-readable JSON.

        Returns ``200`` and ``{"ok": true, ...}`` only when every check
        passes. Returns ``503`` with ``{"ok": false, ...}`` (and the
        failing check set to ``false``) otherwise. The body is JSON so
        orchestrators and runbooks can parse it without scraping text.

        Checks:

        * ``db_ok`` — the per-request SQLite connection answers
          ``SELECT 1``.
        * ``secret_key_ok`` — the active Flask ``SECRET_KEY`` is
          acceptable for the resolved ``environment``. The
          well-known development default is only considered
          acceptable in ``development`` / ``testing``; in
          ``production`` it is reported as ``False`` so an
          operator / orchestrator can take the instance out of
          rotation. (The factory also refuses to *start* in
          production with the dev default, so the only way to
          reach this branch is a hot reload that replaced the key
          — we still want the probe to be honest.)
        * ``using_dev_secret`` — convenience flag for operators /
          dashboards. Independent of environment so dashboards
          can show "this instance is using the development
          SECRET_KEY" without parsing logic.
        * ``environment`` — the resolved environment name
          (``production`` / ``development`` / ``testing``).
        """
        env = app.config.get("WORKBENCH_ENV", "testing")
        secret_key = app.config.get("SECRET_KEY", "")
        using_dev_secret = secret_key in _INSECURE_PRODUCTION_SECRETS
        # The dev secret is only a readiness failure in production
        # mode. In dev/testing the factory already accepted it; the
        # probe must reflect the running policy, not the production
        # policy applied universally.
        secret_key_ok = (not using_dev_secret) or (env != "production")

        db_ok = False
        db_error: Optional[str] = None
        try:
            conn = get_db()
            row = conn.execute("SELECT 1 AS ok").fetchone()
            db_ok = bool(row and row["ok"] == 1)
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            db_error = str(exc)
            db_ok = False

        all_ok = db_ok and secret_key_ok
        body = {
            "ok": all_ok,
            "db_ok": db_ok,
            "secret_key_ok": secret_key_ok,
            "environment": env,
            "using_dev_secret": using_dev_secret,
        }
        if db_error is not None:
            body["db_error"] = db_error
        return jsonify(body), (200 if all_ok else 503)

    app.add_template_global(extract_message_body, name="message_body")

    # --- Chat UX bubble helpers ---------------------------------------
    # These helpers are used by message_row.html to translate a
    # ``RoutedMessage`` into a user-facing role/avatar/display-name. They
    # are exposed as Jinja globals so any template can use them.
    from agent_workbench.web import bubble_helpers  # noqa: E402  (import after sys.path is set)

    app.add_template_global(bubble_helpers.bubble_role, name="bubble_role")
    app.add_template_global(bubble_helpers.bubble_initials, name="bubble_initials")
    app.add_template_global(bubble_helpers.bubble_display_name, name="bubble_display_name")
    app.add_template_global(bubble_helpers.bubble_time, name="bubble_time")

    # JSON-safe template filter: parses a string and returns a dict,
    # or {} if parsing fails.  Used by message_row.html to branch on
    # the payload envelope.
    import json as _json
    from datetime import datetime as _dt

    def _from_json_loads(value):
        if not value:
            return {}
        try:
            data = _json.loads(value)
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _datetime_filter(timestamp):
        """Format a Unix timestamp as a human-readable date string."""
        if not timestamp:
            return ""
        try:
            return _dt.fromtimestamp(float(timestamp)).strftime("%b %d, %H:%M")
        except (TypeError, ValueError, OSError):
            return ""

    app.add_template_filter(_from_json_loads, name="from_json_loads")
    app.add_template_filter(_datetime_filter, name="datetime")

    # Register blueprints.
    from agent_workbench.web.forks import forks_bp
    from agent_workbench.web.reviews import reviews_bp
    from agent_workbench.web.permissions import permissions_bp
    from agent_workbench.web.task_specs import bp as task_specs_bp
    from agent_workbench.web.runs import bp as runs_bp
    from agent_workbench.web.channels import bp as channels_bp
    from agent_workbench.web.sessions import bp as sessions_bp
    from agent_workbench.web.messages import bp as messages_bp
    from agent_workbench.web.settings import bp as settings_bp

    app.register_blueprint(forks_bp)
    app.register_blueprint(reviews_bp)
    app.register_blueprint(permissions_bp)
    app.register_blueprint(task_specs_bp)
    app.register_blueprint(runs_bp)
    app.register_blueprint(channels_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(settings_bp)

    # Friendly 404.
    @app.errorhandler(404)
    def _not_found(_e: Exception) -> str:
        return render_template("error.html", message="Not found"), 404

    return app


# ---------------------------------------------------------------------------
# Per-request helpers
# ---------------------------------------------------------------------------


def get_db() -> sqlite3.Connection:
    """Return the per-request SQLite connection.

    Raises
    ------
    RuntimeError
        If called outside of a request context (e.g. from CLI scripts).
    """
    if "db" not in g:
        raise RuntimeError(
            "Database connection is not available outside a request. "
            "Use agent_workbench.db.get_connection directly in non-web code."
        )
    return g.db


def get_orchestrator() -> OrchestratorService:
    """Return an :class:`OrchestratorService` bound to the request DB."""
    return OrchestratorService(get_db())


def get_session_service() -> SessionService:
    """Return a :class:`SessionService` bound to the request DB."""
    return SessionService(get_db())


def get_profile_service() -> ProfileService:
    """Return a :class:`ProfileService` bound to the request DB."""
    return ProfileService(get_db())


def get_provider_service() -> ProviderService:
    """Return a :class:`ProviderService` bound to the request DB."""
    return ProviderService(get_db())


def get_role_service() -> RoleService:
    """Return a :class:`RoleService` bound to the request DB."""
    return RoleService(get_db())


def get_participant_service() -> ParticipantService:
    """Return a :class:`ParticipantService` bound to the request DB."""
    return ParticipantService(get_db())


def get_routing_service() -> RoutingService:
    """Return a :class:`RoutingService` bound to the request DB."""
    return RoutingService(get_db())
