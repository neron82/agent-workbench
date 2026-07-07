"""Fork UX blueprint.

Implements the structured session-fork workflow defined in
``04_SESSION_FORKING.md`` and surfaced in ``08_UI_WORKFLOW.md`` §8.
The blueprint owns:

* ``GET  /sessions/<session_id>/fork`` — show the fork creation form.
* ``POST /sessions/<session_id>/fork`` — create the fork via
  :class:`ForkService`.
* ``GET  /forks/<fork_id>`` — show the fork detail view (parent
  linkage, summary, inherited context, checkpoint).

Templates live in ``agent_workbench/web/templates/``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from flask import Blueprint, abort, flash, get_flashed_messages, redirect, render_template, request, url_for

from agent_workbench.models.session_extension import SESSION_TYPES, SessionExtensionRepository
from agent_workbench.services.fork_service import ForkService
from agent_workbench.web.app import get_db


forks_bp = Blueprint("forks", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_optional_json(name: str, raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse a ``textarea``-submitted JSON blob.

    The fork form lets users paste a JSON object into the
    ``decisions``/``assumptions``/``open_questions`` textareas. Empty
    input maps to ``None``. Non-empty input must parse as a JSON
    object; otherwise we raise :class:`ValueError` and the route
    surfaces the message as a validation error.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON ({exc.msg})") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object (got {type(parsed).__name__})")
    return parsed


def _next_session_type(current: str) -> str:
    """Pick a sensible default for the "proposed new type" select.

    The default is the *next* session type in the chat → research →
    work progression so the typical user flow is one click away.
    Anything outside the canonical set falls back to ``chat``.
    """
    progression = ("chat", "research", "work")
    if current not in progression:
        return "chat"
    idx = progression.index(current)
    return progression[min(idx + 1, len(progression) - 1)]


def _load_parent(conn, session_id: str):
    repo = SessionExtensionRepository(conn)
    parent = repo.get_by_id(session_id)
    if parent is None:
        abort(404, description=f"Session {session_id!r} not found")
    return parent, repo


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@forks_bp.route("/sessions/<session_id>/fork", methods=["GET"])
def show_fork_form(session_id: str):
    """Render the fork creation form for ``session_id``."""
    conn = get_db()
    parent, _ = _load_parent(conn, session_id)

    proposed = request.args.get("new_session_type") or _next_session_type(parent.session_type)
    if proposed not in SESSION_TYPES:
        proposed = _next_session_type(parent.session_type)

    return render_template(
        "fork_form.html",
        parent=parent,
        session_types=list(SESSION_TYPES),
        proposed_session_type=proposed,
        form_data={
            "summary": "",
            "fork_reason": "",
            "decisions": "",
            "assumptions": "",
            "open_questions": "",
            "new_session_type": proposed,
        },
        error=None,
    )


@forks_bp.route("/sessions/<session_id>/fork", methods=["POST"])
def create_fork(session_id: str):
    """Create a structured fork for ``session_id``.

    Form fields
    -----------
    new_session_type : str
        Target type.  Must be one of ``chat``/``research``/``work``.
    summary : str
        Required non-empty initial summary.
    fork_reason : str
        Free-text reason for the fork.
    decisions, assumptions, open_questions : str
        Optional JSON-encoded objects.
    child_session_id : str
        Optional caller-supplied id for the new child session. When
        omitted a fresh UUID is allocated.
    """
    conn = get_db()
    parent, _ = _load_parent(conn, session_id)

    new_type = (request.form.get("new_session_type") or "").strip()
    summary = (request.form.get("summary") or "").strip()
    fork_reason = (request.form.get("fork_reason") or "").strip()
    child_session_id = (request.form.get("child_session_id") or "").strip() or uuid.uuid4().hex

    # Re-render the form on validation errors so the user does not
    # lose their input.
    def _render_error(message: str, code: int = 400):
        return (
            render_template(
                "fork_form.html",
                parent=parent,
                session_types=list(SESSION_TYPES),
                proposed_session_type=new_type or _next_session_type(parent.session_type),
                form_data={
                    "summary": summary,
                    "fork_reason": fork_reason,
                    "decisions": request.form.get("decisions") or "",
                    "assumptions": request.form.get("assumptions") or "",
                    "open_questions": request.form.get("open_questions") or "",
                    "new_session_type": new_type,
                },
                error=message,
            ),
            code,
        )

    if new_type not in SESSION_TYPES:
        return _render_error(
            f"Invalid new_session_type {new_type!r}. Must be one of {SESSION_TYPES}."
        )
    if not summary:
        return _render_error("summary is required and must be non-empty.")

    try:
        decisions = _parse_optional_json("decisions", request.form.get("decisions"))
        assumptions = _parse_optional_json("assumptions", request.form.get("assumptions"))
        open_questions = _parse_optional_json("open_questions", request.form.get("open_questions"))
    except ValueError as exc:
        return _render_error(str(exc))

    service = ForkService(conn)
    try:
        fork_record = service.create_fork(
            parent_session_id=session_id,
            child_session_id=child_session_id,
            new_session_type=new_type,
            fork_reason=fork_reason,
            initiated_by="user",
            summary=summary,
            decisions=decisions,
            assumptions=assumptions,
            open_questions=open_questions,
        )
    except ValueError as exc:
        return _render_error(str(exc))

    flash(f"Fork created (id {fork_record.fork_id[:8]}…).", "success")
    return redirect(url_for("forks.show_fork_detail", fork_id=fork_record.fork_id))


@forks_bp.route("/forks/<fork_id>", methods=["GET"])
def show_fork_detail(fork_id: str):
    """Render the fork detail view.

    The page shows the parent linkage, the captured summary, the
    inherited structured context, and the versioned checkpoint so
    reviewers can tell which context was inherited and which work
    was produced after the fork (spec §11).
    """
    conn = get_db()
    service = ForkService(conn)
    try:
        fork = service.get_fork(fork_id)
    except LookupError:
        abort(404, description=f"Fork {fork_id!r} not found")

    parent, _ = _load_parent(conn, fork.parent_session_id)
    child, _ = _load_parent(conn, fork.child_session_id)

    flashes = get_flashed_messages(with_categories=True)
    return render_template(
        "fork_detail.html",
        fork=fork,
        parent=parent,
        child=child,
        flashes=flashes,
    )
