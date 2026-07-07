"""Permission request approval blueprint.

Implements the user-facing approval surfaces for tool / command /
file / remote / task permission requests. Spec:

* ``GET  /runs/<harness_run_id>/permissions`` — list pending
  permission requests for a run.
* ``POST /runs/<harness_run_id>/permissions/<permission_request_id>/approve``
* ``POST /runs/<harness_run_id>/permissions/<permission_request_id>/deny``

A pending request is one whose ``decision == 'pending'``. Resolved
requests are still shown (read-only) so reviewers can see what was
approved/denied.  Requests with
``escalated_from_auto_approve=True`` are flagged with an "Escalated"
badge per spec §5 (sensitive sessions may escalate to per-tool
confirmation even when lower layers are auto-approve capable).
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from agent_workbench.models.permission_request import PermissionRequestRepository
from agent_workbench.web.app import get_db


permissions_bp = Blueprint("permissions", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DECISIONS = ("pending", "approved", "denied", "expired")


def _load_harness_run(conn, harness_run_id: str):
    row = conn.execute(
        "SELECT harness_run_id, workspace_id, session_id, harness_type, status "
        "FROM harness_runs WHERE harness_run_id = ?",
        (harness_run_id,),
    ).fetchone()
    if row is None:
        abort(404, description=f"Harness run {harness_run_id!r} not found")
    return row


def _load_request(repo: PermissionRequestRepository, permission_request_id: str):
    pr = repo.get_by_id(permission_request_id)
    if pr is None:
        abort(404, description=f"Permission request {permission_request_id!r} not found")
    return pr


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@permissions_bp.route("/runs/<harness_run_id>/permissions", methods=["GET"])
def list_permission_requests(harness_run_id: str):
    """Render the permission-request list for a harness run."""
    conn = get_db()
    run = _load_harness_run(conn, harness_run_id)

    repo = PermissionRequestRepository(conn)
    all_requests = repo.list_by_harness_run(harness_run_id)
    pending = [r for r in all_requests if r.decision == "pending"]
    resolved = [r for r in all_requests if r.decision != "pending"]

    return render_template(
        "permission_requests.html",
        run=run,
        harness_run_id=harness_run_id,
        pending=pending,
        resolved=resolved,
        all_requests=all_requests,
    )


@permissions_bp.route(
    "/runs/<harness_run_id>/permissions/<permission_request_id>/approve",
    methods=["POST"],
)
def approve_permission_request(harness_run_id: str, permission_request_id: str):
    """Approve a pending permission request."""
    conn = get_db()
    _load_harness_run(conn, harness_run_id)

    repo = PermissionRequestRepository(conn)
    pr = _load_request(repo, permission_request_id)

    if pr.harness_run_id != harness_run_id:
        abort(
            400,
            description=(
                f"Permission request {permission_request_id!r} does not belong to "
                f"run {harness_run_id!r}"
            ),
        )

    if pr.decision != "pending":
        flash(
            f"Permission request {permission_request_id[:8]}… is already "
            f"{pr.decision}; cannot re-approve.",
            "warning",
        )
        return redirect(
            url_for("permissions.list_permission_requests", harness_run_id=harness_run_id)
        )

    updated = repo.update_decision(permission_request_id, decision="approved")
    if updated is None:
        abort(404, description="Permission request disappeared mid-approval")
    flash(
        f"Permission request {permission_request_id[:8]}… approved "
        f"(scope={pr.scope}).",
        "success",
    )
    return redirect(
        url_for("permissions.list_permission_requests", harness_run_id=harness_run_id)
    )


@permissions_bp.route(
    "/runs/<harness_run_id>/permissions/<permission_request_id>/deny",
    methods=["POST"],
)
def deny_permission_request(harness_run_id: str, permission_request_id: str):
    """Deny a pending permission request."""
    conn = get_db()
    _load_harness_run(conn, harness_run_id)

    repo = PermissionRequestRepository(conn)
    pr = _load_request(repo, permission_request_id)

    if pr.harness_run_id != harness_run_id:
        abort(
            400,
            description=(
                f"Permission request {permission_request_id!r} does not belong to "
                f"run {harness_run_id!r}"
            ),
        )

    if pr.decision != "pending":
        flash(
            f"Permission request {permission_request_id[:8]}… is already "
            f"{pr.decision}; cannot re-deny.",
            "warning",
        )
        return redirect(
            url_for("permissions.list_permission_requests", harness_run_id=harness_run_id)
        )

    updated = repo.update_decision(permission_request_id, decision="denied")
    if updated is None:
        abort(404, description="Permission request disappeared mid-deny")
    flash(
        f"Permission request {permission_request_id[:8]}… denied "
        f"(scope={pr.scope}).",
        "success",
    )
    return redirect(
        url_for("permissions.list_permission_requests", harness_run_id=harness_run_id)
    )
