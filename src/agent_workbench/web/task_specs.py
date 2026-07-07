"""Blueprint for the TaskSpec approval gate.

Routes
------
* ``GET  /task-specs/<task_spec_id>``             — view a task spec
* ``POST /task-specs/<task_spec_id>/approve``     — approve
* ``POST /task-specs/<task_spec_id>/reject``      — reject
* ``GET  /sessions/<session_id>/task-spec``       — creation form
* ``POST /sessions/<session_id>/task-spec``       — create from research session
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
import sqlite3

from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.task_spec import TaskSpec, TaskSpecRepository
from agent_workbench.web.app import get_db

bp = Blueprint("task_specs", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    """Return the active per-request SQLite connection.

    Delegates to :func:`agent_workbench.web.app.get_db`, which uses
    ``flask.g`` for per-request connection management.  When a test
    has injected a connection via ``app.config["WORKBENCH_DB_CONN"]``,
    that connection is reused.
    """
    return get_db()


def _parse_json_field(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse a JSON-typed form field.

    Empty / whitespace-only inputs are treated as ``None`` (cleared).
    Invalid JSON is a 400 — the user typed something that doesn't parse.
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        abort(400, description=f"Invalid JSON in form field: {exc.msg}")


# ---------------------------------------------------------------------------
# View / Approve / Reject
# ---------------------------------------------------------------------------


@bp.route("/task-specs/<task_spec_id>", methods=["GET"])
def view(task_spec_id: str):
    """Render a task spec with objective, scope, criteria, and approval state."""
    repo = TaskSpecRepository(_conn())
    spec = repo.get_by_id(task_spec_id)
    if spec is None:
        abort(404, description=f"TaskSpec {task_spec_id} not found")
    return render_template("task_spec_view.html", spec=spec)


@bp.route("/task-specs/<task_spec_id>/approve", methods=["POST"])
def approve(task_spec_id: str):
    """Mark a task spec as approved (UI gate action)."""
    repo = TaskSpecRepository(_conn())
    spec = repo.get_by_id(task_spec_id)
    if spec is None:
        abort(404, description=f"TaskSpec {task_spec_id} not found")
    # Per the schema (001_initial_schema.py) the legal approval
    # values are 'draft', 'ready_for_review', 'approved', 'rejected',
    # 'superseded'.  The web form only ever sets 'draft'; reviewers
    # may move it to 'ready_for_review' from a future review screen.
    # Approval is a one-way gate per UI section 5.
    if spec.approval_status not in ("draft", "ready_for_review"):
        # We refuse silently rather than 409 — the form is hidden when
        # the spec isn't in a reviewable state, so reaching this in an
        # un-reviewable state is a UI bug, not a user error.
        flash(
            f"TaskSpec is already {spec.approval_status}; cannot approve.",
            "warning",
        )
        return redirect(url_for("task_specs.view", task_spec_id=task_spec_id))
    repo.update_approval_status(task_spec_id, approval_status="approved")
    flash("TaskSpec approved.", "success")
    return redirect(url_for("task_specs.view", task_spec_id=task_spec_id))


@bp.route("/task-specs/<task_spec_id>/reject", methods=["POST"])
def reject(task_spec_id: str):
    """Mark a task spec as rejected."""
    repo = TaskSpecRepository(_conn())
    spec = repo.get_by_id(task_spec_id)
    if spec is None:
        abort(404, description=f"TaskSpec {task_spec_id} not found")
    # See note in approve() — schema-level approval_status values.
    if spec.approval_status not in ("draft", "ready_for_review"):
        flash(
            f"TaskSpec is already {spec.approval_status}; cannot reject.",
            "warning",
        )
        return redirect(url_for("task_specs.view", task_spec_id=task_spec_id))
    repo.update_approval_status(task_spec_id, approval_status="rejected")
    flash("TaskSpec rejected.", "info")
    return redirect(url_for("task_specs.view", task_spec_id=task_spec_id))


# ---------------------------------------------------------------------------
# Create from research session
# ---------------------------------------------------------------------------


@bp.route("/sessions/<session_id>/task-spec", methods=["GET"])
def new_form(session_id: str):
    """Render the task spec creation form bound to a research session."""
    sessions = SessionExtensionRepository(_conn())
    session = sessions.get_by_id(session_id)
    if session is None:
        abort(404, description=f"Session {session_id} not found")
    return render_template("task_spec_form.html", session=session)


@bp.route("/sessions/<session_id>/task-spec", methods=["POST"])
def create_from_session(session_id: str):
    """Create a task spec from the form submission."""
    sessions = SessionExtensionRepository(_conn())
    session = sessions.get_by_id(session_id)
    if session is None:
        abort(404, description=f"Session {session_id} not found")

    objective = (request.form.get("objective") or "").strip()
    if not objective:
        flash("Objective is required.", "error")
        return redirect(url_for("task_specs.new_form", session_id=session_id))

    risk_level = (request.form.get("risk_level") or "").strip() or None
    acceptance_criteria = _parse_json_field(request.form.get("acceptance_criteria_json"))
    scope_in = _parse_json_field(request.form.get("scope_in_json"))
    scope_out = _parse_json_field(request.form.get("scope_out_json"))
    constraints = _parse_json_field(request.form.get("constraints_json"))

    repo = TaskSpecRepository(_conn())
    spec = repo.create(
        workspace_id=session.workspace_id,
        source_session_id=session_id,
        objective=objective,
        scope_in=scope_in,
        scope_out=scope_out,
        acceptance_criteria=acceptance_criteria,
        constraints=constraints,
        risk_level=risk_level,
        approval_status="draft",
    )

    # Link the new spec back to the source session so downstream
    # structured forks (e.g. research → work) can carry it forward as
    # part of the session's context.  This is a small follow-up write;
    # the spec is already durable on its own, and the link is only
    # used for the fork-inheritance contract in
    # ``SessionService.transition_session_type``.
    sessions.update_task_spec(session_id, task_spec_id=spec.task_spec_id)

    flash("TaskSpec created — review and approve to dispatch.", "success")
    return redirect(url_for("task_specs.view", task_spec_id=spec.task_spec_id))
