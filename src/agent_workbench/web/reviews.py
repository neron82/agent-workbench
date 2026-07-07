"""Reviews and replays blueprint.

Implements the review/replay visibility surfaces defined in
``08_UI_WORKFLOW.md`` §11 ("Replay / review decision"):

* ``GET  /sessions/<session_id>/reviews`` — list reviews for a
  session (a session-level review target — ``target_kind='session'``).
* ``POST /sessions/<session_id>/reviews`` — create a new review
  record.
* ``POST /runs/<harness_run_id>/reviews`` — create a new review
  record targeted at a harness run (the run-level review record that
  the Phase 7 verification surface aggregates).
* ``GET  /runs/<harness_run_id>/replay`` — show the replay timeline
  for a harness run.
* ``POST /runs/<harness_run_id>/replay`` — create a replay record.

Review surfaces must make replay equivalence clear (spec §11): we
show the canonical note "Replay equivalence means equivalent final
state and reviewer-judged outcome, not identical tool-call sequence."
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from agent_workbench.models.replay_record import ReplayRecord, ReplayRecordRepository
from agent_workbench.models.review_record import ReviewRecordRepository
from agent_workbench.web.app import get_db


reviews_bp = Blueprint("reviews", __name__)


# Canonical replay equivalence note — spec §11.
REPLAY_EQUIVALENCE_NOTE = (
    "Replay equivalence means equivalent final state and reviewer-judged "
    "outcome, not identical tool-call sequence."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Allowed verdicts.  These mirror the CHECK constraint on
# ``review_records.verdict``.
_REVIEW_VERDICTS = ("pass", "fail", "conditional", "blocked")
_REVIEW_TARGET_KINDS = ("task_spec", "artifact", "harness_run", "session")
_REPLAY_OUTCOMES = ("completed", "diverged", "aborted")

def _parse_optional_json(name: str, raw: Optional[str]) -> Optional[Dict[str, Any]]:
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


def _load_session_extension(conn, session_id: str):
    """Return a session_extensions row or 404."""
    row = conn.execute(
        "SELECT session_id, workspace_id, session_type, status "
        "FROM session_extensions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        abort(404, description=f"Session {session_id!r} not found")
    return row


def _load_harness_run(conn, harness_run_id: str):
    row = conn.execute(
        "SELECT harness_run_id, workspace_id, session_id, harness_type, status "
        "FROM harness_runs WHERE harness_run_id = ?",
        (harness_run_id,),
    ).fetchone()
    if row is None:
        abort(404, description=f"Harness run {harness_run_id!r} not found")
    return row


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


@reviews_bp.route("/sessions/<session_id>/reviews", methods=["GET"])
def list_reviews(session_id: str):
    """List all reviews for a session."""
    conn = get_db()
    session_row = _load_session_extension(conn, session_id)

    repo = ReviewRecordRepository(conn)
    reviews = repo.list_by_target("session", session_id)

    return render_template(
        "review_list.html",
        session=session_row,
        session_id=session_id,
        reviews=reviews,
        verdicts=list(_REVIEW_VERDICTS),
        form_data={
            "verdict": "pass",
            "findings_ref": "",
            "criteria_eval": "",
            "reviewer_binding_id": "",
        },
        error=None,
    )


@reviews_bp.route("/sessions/<session_id>/reviews", methods=["POST"])
def create_review(session_id: str):
    """Create a new review record for a session."""
    conn = get_db()
    session_row = _load_session_extension(conn, session_id)
    workspace_id = session_row["workspace_id"]

    verdict = (request.form.get("verdict") or "").strip()
    findings_ref = (request.form.get("findings_ref") or "").strip() or None
    reviewer_binding_id = (request.form.get("reviewer_binding_id") or "").strip() or None
    criteria_eval_raw = request.form.get("criteria_eval") or ""

    def _render_error(message: str, code: int = 400):
        return (
            render_template(
                "review_list.html",
                session=session_row,
                session_id=session_id,
                reviews=ReviewRecordRepository(conn).list_by_target("session", session_id),
                verdicts=list(_REVIEW_VERDICTS),
                form_data={
                    "verdict": verdict,
                    "findings_ref": findings_ref or "",
                    "criteria_eval": criteria_eval_raw,
                    "reviewer_binding_id": reviewer_binding_id or "",
                },
                error=message,
            ),
            code,
        )

    if verdict not in _REVIEW_VERDICTS:
        return _render_error(
            f"Invalid verdict {verdict!r}. Must be one of {_REVIEW_VERDICTS}."
        )

    try:
        criteria_eval = _parse_optional_json("criteria_eval", criteria_eval_raw)
    except ValueError as exc:
        return _render_error(str(exc))

    repo = ReviewRecordRepository(conn)
    review = repo.create(
        workspace_id=workspace_id,
        target_kind="session",
        target_id=session_id,
        reviewer_binding_id=reviewer_binding_id,
        verdict=verdict,
        findings_ref=findings_ref,
        criteria_eval=criteria_eval,
    )

    flash(f"Review {review.review_id[:8]}… recorded (verdict={verdict}).", "success")
    return redirect(url_for("reviews.list_reviews", session_id=session_id))


@reviews_bp.route("/runs/<harness_run_id>/reviews", methods=["POST"])
def create_run_review(harness_run_id: str):
    """Create a new review record for a harness run.

    The Phase 7 verification surface aggregates reviews that target
    the harness run directly (``target_kind='harness_run'``), its
    artifacts, or its task spec.  Session-level reviews alone are not
    enough to promote a run to ``verification_ready=True``; the
    orchestrator / reviewer needs to be able to record a review
    against the *run* itself.

    On success the route redirects to the run detail page so the
    user can see the new review reflected in the verification
    surface immediately.
    """
    conn = get_db()
    run = _load_harness_run(conn, harness_run_id)
    workspace_id = run["workspace_id"]

    verdict = (request.form.get("verdict") or "").strip()
    findings_ref = (request.form.get("findings_ref") or "").strip() or None
    reviewer_binding_id = (
        request.form.get("reviewer_binding_id") or ""
    ).strip() or None
    criteria_eval_raw = request.form.get("criteria_eval") or ""

    if verdict not in _REVIEW_VERDICTS:
        flash(
            f"Invalid verdict {verdict!r}. Must be one of {_REVIEW_VERDICTS}.",
            "error",
        )
        return redirect(
            url_for("runs.detail", harness_run_id=harness_run_id)
        )

    try:
        criteria_eval = _parse_optional_json("criteria_eval", criteria_eval_raw)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(
            url_for("runs.detail", harness_run_id=harness_run_id)
        )

    repo = ReviewRecordRepository(conn)
    review = repo.create(
        workspace_id=workspace_id,
        target_kind="harness_run",
        target_id=harness_run_id,
        reviewer_binding_id=reviewer_binding_id,
        verdict=verdict,
        findings_ref=findings_ref,
        criteria_eval=criteria_eval,
    )

    flash(
        f"Review {review.review_id[:8]}… recorded for run "
        f"(verdict={verdict}).",
        "success",
    )
    return redirect(
        url_for("runs.detail", harness_run_id=harness_run_id)
    )


# ---------------------------------------------------------------------------
# Replays
# ---------------------------------------------------------------------------


@reviews_bp.route("/runs/<harness_run_id>/replay", methods=["GET"])
def show_replay(harness_run_id: str):
    """Show the replay timeline for a harness run."""
    conn = get_db()
    run = _load_harness_run(conn, harness_run_id)

    repo = ReplayRecordRepository(conn)
    # Replay records are keyed off the source session, not the run
    # directly, so we look up by the run's ``session_id`` and filter
    # to entries that match this run when populated.
    replays = [
        r
        for r in repo.list_by_session(run["session_id"])
        if r.source_harness_run_id is None or r.source_harness_run_id == harness_run_id
    ]

    return render_template(
        "replay_view.html",
        run=run,
        harness_run_id=harness_run_id,
        replays=replays,
        replay_outcomes=list(_REPLAY_OUTCOMES),
        replay_equivalence_note=REPLAY_EQUIVALENCE_NOTE,
        form_data={
            "replay_scope": "",
            "outcome": "completed",
        },
        error=None,
    )


@reviews_bp.route("/runs/<harness_run_id>/replay", methods=["POST"])
def create_replay(harness_run_id: str):
    """Create a new replay record for a harness run.

    A replay record requires a ``fork_id`` (per the schema). When
    the caller does not supply one we mint a no-op fork for this
    run's session so the replay row can persist standalone. Callers
    in the real product supply the fork that initiated the replay.
    """
    conn = get_db()
    run = _load_harness_run(conn, harness_run_id)
    session_id = run["session_id"]

    replay_scope = (request.form.get("replay_scope") or "").strip()
    outcome = (request.form.get("outcome") or "completed").strip()
    fork_id = (request.form.get("fork_id") or "").strip()

    def _render_error(message: str, code: int = 400):
        repo = ReplayRecordRepository(conn)
        replays = [
            r
            for r in repo.list_by_session(session_id)
            if r.source_harness_run_id is None or r.source_harness_run_id == harness_run_id
        ]
        return (
            render_template(
                "replay_view.html",
                run=run,
                harness_run_id=harness_run_id,
                replays=replays,
                replay_outcomes=list(_REPLAY_OUTCOMES),
                replay_equivalence_note=REPLAY_EQUIVALENCE_NOTE,
                form_data={
                    "replay_scope": replay_scope,
                    "outcome": outcome,
                },
                error=message,
            ),
            code,
        )

    if outcome not in _REPLAY_OUTCOMES:
        return _render_error(
            f"Invalid outcome {outcome!r}. Must be one of {_REPLAY_OUTCOMES}."
        )

    if not fork_id:
        # Mint a minimal replay-kind fork on the fly so the FK
        # constraint is satisfied.  This mirrors the test-only path
        # where a replay is created before any real fork exists.
        fork_id = uuid.uuid4().hex
        checkpoint = {
            "version": 1,
            "source_session_id": session_id,
            "source_message_offset": 0,
        }
        conn.execute(
            "INSERT INTO fork_records ("
            "fork_id, parent_session_id, child_session_id, fork_kind, "
            "fork_reason, initiated_by, summary_ref, "
            "bootstrap_context_role_internal, checkpoint_json, created_at) "
            "VALUES (?, ?, ?, 'replay', ?, 'system', NULL, "
            "'fork_context', ?, strftime('%s', 'now'))",
            (
                fork_id,
                session_id,
                session_id,  # replay kind reuses the source session as child
                f"auto-created for replay of run {harness_run_id}",
                json.dumps(checkpoint),
            ),
        )
        conn.commit()

    # Verify the fork_id actually exists — protects against typos.
    fork_row = conn.execute(
        "SELECT fork_id FROM fork_records WHERE fork_id = ?", (fork_id,)
    ).fetchone()
    if fork_row is None:
        return _render_error(f"fork_id {fork_id!r} does not exist.")

    repo = ReplayRecordRepository(conn)
    replay = repo.create(
        source_session_id=session_id,
        source_harness_run_id=harness_run_id,
        fork_id=fork_id,
        checkpoint={
            "version": 1,
            "source_session_id": session_id,
            "source_message_offset": 0,
        },
        replay_scope=replay_scope,
        outcome=outcome,
    )

    flash(f"Replay {replay.replay_id[:8]}… recorded (outcome={outcome}).", "success")
    return redirect(url_for("reviews.show_replay", harness_run_id=harness_run_id))
