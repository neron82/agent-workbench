"""Dependency-free CSRF protection for Flask.

Uses ``secrets.token_hex(32)`` (256-bit) for token generation, stores the
token in the signed Flask session, and validates via
:func:`hmac.compare_digest` to prevent timing attacks.

Accepts the token from a form field named ``csrf_token`` or an HTTP header
named ``X-CSRF-Token``.  Every non-GET/HEAD/OPTIONS/TRACE request is
validated; there are no unsafe-route exemptions.

Returns a 403 response on failure:
* JSON body when ``request.is_json`` is true, or the request carries
  ``X-Requested-With: fetch``, or ``Accept: application/json``.
* Plain HTML 403 page otherwise (no redirect, so the original action is
  never accidentally replayed).

Usage
-----
Call ``init_csrf(app)`` inside the app factory **before** registering
blueprints.  A ``csrf_token()`` Jinja global is registered automatically
so templates can write ``<input type="hidden" name="csrf_token" value="{{
csrf_token() }}">``.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Optional

from flask import Flask, jsonify, request, session as flask_session

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Session key used to store the CSRF token.
_SESSION_KEY = "_csrf_token"

# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _get_or_create_token() -> str:
    """Return the CSRF token stored in the session, creating one if absent.

    The token is a 64-character hex string (256 bits of entropy).
    """
    token: Optional[str] = flask_session.get(_SESSION_KEY)
    if not isinstance(token, str):
        token = secrets.token_hex(32)  # 256 bits
        flask_session[_SESSION_KEY] = token
    return token


def _extract_token_from_request() -> Optional[str]:
    """Read the CSRF token from the request — form field or header.

    The form field ``csrf_token`` is checked first (for HTML forms), then
    the ``X-CSRF-Token`` header (for AJAX / fetch requests).
    """
    token: Optional[str] = request.form.get("csrf_token")
    if token:
        return token
    token = request.headers.get("X-CSRF-Token")
    return token


# ---------------------------------------------------------------------------
# Jinja helper
# ---------------------------------------------------------------------------


def csrf_token() -> str:
    """Jinja-accessible helper that returns the current session's CSRF token.

    Usage in templates::

        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    """
    return _get_or_create_token()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


def _should_skip_csrf() -> bool:
    """Return True when the current request should skip CSRF validation.

    Safe methods (GET, HEAD, OPTIONS, TRACE) are always skipped.
    """
    if request.method in _SAFE_METHODS:
        return True
    return False


def _is_json_request() -> bool:
    """Heuristic: does the client expect a JSON response?"""
    if request.is_json:
        return True
    if request.headers.get("X-Requested-With") == "fetch":
        return True
    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        return True
    return False


def _csrf_error() -> tuple:
    """Return a 403 response appropriate for the request type."""
    if _is_json_request():
        return jsonify({"error": "CSRF token missing or invalid"}), 403
    return (
        "<!doctype html><title>403 Forbidden</title>"
        "<h1>Forbidden</h1><p>CSRF token missing or invalid. "
        "Please reload the page and try again.</p>",
        403,
        {"Content-Type": "text/html; charset=utf-8"},
    )


def _validate_csrf() -> Optional[tuple]:
    """Before-request handler: validate the CSRF token on unsafe methods.

    Returns a 403 response tuple when validation fails, or ``None`` to
    let Flask continue processing the request.
    """
    if _should_skip_csrf():
        return None

    # A valid token must already exist in the signed browser session.
    # Do not create one on an unsafe request: rejection stays side-effect free.
    expected = flask_session.get(_SESSION_KEY)
    if not isinstance(expected, str):
        return _csrf_error()
    actual = _extract_token_from_request()

    if actual is None or not hmac.compare_digest(expected, actual):
        return _csrf_error()

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_csrf(app: Flask) -> None:
    """Register CSRF protection on *app*.

    Call this **before** registering blueprints so the before-request
    handler runs before any blueprint handler.

    Registers:
    * A ``before_request`` handler that validates the CSRF token on every
      non-safe request.
    * A ``csrf_token()`` Jinja global for use in templates.
    """
    app.before_request(_validate_csrf)
    app.add_template_global(csrf_token, name="csrf_token")
