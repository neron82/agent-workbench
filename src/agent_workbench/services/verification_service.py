"""VerificationService — cross-harness verification surface.

This service composes the four repositories that together make a
:class:`HarnessRun` trustworthy enough to treat as a verified product
output:

* :class:`~agent_workbench.models.harness_run.HarnessRunRepository`
  — the run itself and its lifecycle status.
* :class:`~agent_workbench.models.artifact.ArtifactRepository`
  — the artifacts the run produced, including the ``content_hash``
  needed to prove they are not tampered with after the fact.
* :class:`~agent_workbench.models.review_record.ReviewRecordRepository`
  — reviewer judgments on the run (or the artifacts / task spec
  associated with it). The presence of at least one review is what
  promotes a run from "executed" to "reviewable evidence".
* :class:`~agent_workbench.models.replay_record.ReplayRecordRepository`
  — replay records that prove the run can be re-executed to an
  equivalent end state.

Together these four pieces form the *verification surface* for a
run / session. The service exposes two projection helpers
(:meth:`get_run_verification_surface`,
:meth:`get_session_verification_surface`) and one explainer
(:meth:`explain_blockers`).

Design notes
------------
* Replay equivalence follows the UI spec §11 rule: *equivalent final
  state and reviewer-judged outcome, not identical tool-call
  sequence*. The text on the surface uses that exact phrasing so the
  UI can render it verbatim without re-phrasing.
* Verification readiness is a strict AND of:

  1. run status is one of ``reviewable``, ``completed``, ``failed``,
     ``cancelled`` (i.e. the run is no longer active);
  2. at least one review record exists for the run;
  3. every artifact linked to the run has a non-null ``content_hash``.

  This is the minimum evidence needed to claim a run is "verified"
  for cross-harness use.
* The service is **service-only**: it never touches Flask, the
  templates, or any web route. Phase 7 explicitly forbids web wiring
  here; the surface is consumed by the UI in a later phase.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from agent_workbench.models.artifact import Artifact, ArtifactRepository
from agent_workbench.models.harness_run import HarnessRun, HarnessRunRepository
from agent_workbench.models.replay_record import ReplayRecord, ReplayRecordRepository
from agent_workbench.models.review_record import ReviewRecord, ReviewRecordRepository


# Harness run statuses that are eligible for verification. Anything
# that is still in flight (queued/starting/running/blocked/stopping)
# is not yet ready — there is no final state to verify.
VERIFIABLE_RUN_STATUSES = frozenset(
    {"reviewable", "completed", "failed", "cancelled"}
)

# Replay equivalence rule per spec 03_DOMAIN_MODEL.md §2 (ReplayRecord)
# and 08_UI_WORKFLOW.md §11. The verification surface echoes this
# wording verbatim in the ``replay_equivalence_note`` field.
REPLAY_EQUIVALENCE_NOTE = (
    "Replay equivalence means equivalent final state and "
    "reviewer-judged outcome, not identical tool-call sequence."
)


def _artifact_to_dict(artifact: Artifact) -> Dict[str, Any]:
    """Project an :class:`Artifact` to a JSON-safe dict."""
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_kind": artifact.artifact_kind,
        "title": artifact.title,
        "content_ref": artifact.content_ref,
        "content_hash": artifact.content_hash,
        "predecessor_artifact_id": artifact.predecessor_artifact_id,
        "producer_harness_run_id": artifact.producer_harness_run_id,
        "producer_session_id": artifact.producer_session_id,
        "task_spec_id": artifact.task_spec_id,
        "created_at": artifact.created_at,
        "hash_present": bool(artifact.content_hash),
    }


def _review_to_dict(review: ReviewRecord) -> Dict[str, Any]:
    """Project a :class:`ReviewRecord` to a JSON-safe dict."""
    return {
        "review_id": review.review_id,
        "target_kind": review.target_kind,
        "target_id": review.target_id,
        "reviewer_binding_id": review.reviewer_binding_id,
        "verdict": review.verdict,
        "findings_ref": review.findings_ref,
        "criteria_eval": review.criteria_eval,
        "created_at": review.created_at,
    }


def _replay_to_dict(replay: ReplayRecord) -> Dict[str, Any]:
    """Project a :class:`ReplayRecord` to a JSON-safe dict."""
    return {
        "replay_id": replay.replay_id,
        "source_session_id": replay.source_session_id,
        "source_harness_run_id": replay.source_harness_run_id,
        "fork_id": replay.fork_id,
        "replay_scope": replay.replay_scope,
        "equivalence_rule": replay.equivalence_rule,
        "outcome": replay.outcome,
        "checkpoint": replay.checkpoint,
        "created_at": replay.created_at,
    }


def _latest_review_verdict(reviews: List[ReviewRecord]) -> Optional[str]:
    """Return the verdict of the most recent review, or ``None``."""
    if not reviews:
        return None
    return max(reviews, key=lambda r: r.created_at).verdict


class VerificationService:
    """Service that turns replay/review/artifact evidence into a
    trustworthy verification surface.

    The service is read-only: it never mutates any row. It projects
    the four verification-relevant entities into a single dict per
    run (and per session) that the UI can render directly.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._runs = HarnessRunRepository(conn)
        self._artifacts = ArtifactRepository(conn)
        self._reviews = ReviewRecordRepository(conn)
        self._replays = ReplayRecordRepository(conn)

    # ------------------------------------------------------------------
    # Public API — single-run surface
    # ------------------------------------------------------------------

    def get_run_verification_surface(
        self, harness_run_id: str
    ) -> Dict[str, Any]:
        """Return the verification surface for a single harness run.

        Required keys (per the Phase 7 contract):

        * ``harness_run_id``
        * ``session_id``
        * ``harness_type``
        * ``status``
        * ``artifacts`` (list of artifact dicts)
        * ``reviews`` (list of review dicts)
        * ``replays`` (list of replay dicts)
        * ``latest_review_verdict``
        * ``replay_equivalence_note`` (the exact UI spec §11 wording)
        * ``verification_ready`` (bool)
        * ``blockers`` (list[str])

        Raises
        ------
        LookupError
            If no harness run with the given id exists.
        """
        run = self._runs.get_by_id(harness_run_id)
        if run is None:
            raise LookupError(
                f"No harness run found with id {harness_run_id!r}"
            )

        artifacts = self._list_artifacts_for_run(harness_run_id)
        # Reviews may target the run itself, an artifact produced by
        # the run, or the task spec the run implements. We union all
        # three so the surface shows every piece of review evidence.
        reviews = self._list_reviews_for_run(run, artifacts)
        replays = self._list_replays_for_run(harness_run_id)

        blockers = self._compute_blockers(run, artifacts, reviews)

        return {
            "harness_run_id": run.harness_run_id,
            "session_id": run.session_id,
            "harness_type": run.harness_type,
            "status": run.status,
            "artifacts": [_artifact_to_dict(a) for a in artifacts],
            "reviews": [_review_to_dict(r) for r in reviews],
            "replays": [_replay_to_dict(r) for r in replays],
            "latest_review_verdict": _latest_review_verdict(reviews),
            "replay_equivalence_note": REPLAY_EQUIVALENCE_NOTE,
            "verification_ready": len(blockers) == 0,
            "blockers": blockers,
        }

    # ------------------------------------------------------------------
    # Public API — session-level aggregation
    # ------------------------------------------------------------------

    def get_session_verification_surface(
        self, session_id: str
    ) -> Dict[str, Any]:
        """Aggregate runs, artifacts, reviews and replays for a session.

        The session surface is the union of the per-run surfaces plus
        session-wide counts and the set of *blocking findings* —
        anything that currently blocks verification for at least one
        run in the session.
        """
        runs = self._runs.list_by_session(session_id)
        if not runs:
            return {
                "session_id": session_id,
                "run_count": 0,
                "verification_ready_run_count": 0,
                "artifact_count": 0,
                "review_count": 0,
                "replay_count": 0,
                "latest_review_verdict": None,
                "replay_equivalence_note": REPLAY_EQUIVALENCE_NOTE,
                "blockers": [f"No harness runs found for session {session_id!r}"],
                "verification_ready": False,
                "runs": [],
            }

        # Pre-fetch session-wide lists once — these don't depend on
        # the per-run breakdown.
        session_artifacts = self._artifacts.list_by_session(session_id)
        session_replays = self._replays.list_by_session(session_id)

        run_surfaces: List[Dict[str, Any]] = []
        blocker_set: List[str] = []
        verification_ready_runs = 0
        latest_review_created = -1.0
        latest_review_verdict: Optional[str] = None

        for run in runs:
            surface = self.get_run_verification_surface(run.harness_run_id)
            run_surfaces.append(surface)
            if surface["verification_ready"]:
                verification_ready_runs += 1
            for blocker in surface["blockers"]:
                if blocker not in blocker_set:
                    blocker_set.append(blocker)
            # Track the session-wide latest review verdict.
            for review in surface["reviews"]:
                created = review["created_at"] or 0.0
                if created > latest_review_created:
                    latest_review_created = created
                    latest_review_verdict = review["verdict"]

        # Session-level reviews are reviews whose target is the
        # session itself, plus the per-run reviews we already
        # aggregated (we don't double-count here).
        session_level_reviews = self._reviews.list_by_target(
            "session", session_id
        )
        for review in session_level_reviews:
            created = review.created_at or 0.0
            if created > latest_review_created:
                latest_review_created = created
                latest_review_verdict = review.verdict

        total_artifact_count = len(session_artifacts)
        total_review_count = (
            sum(len(s["reviews"]) for s in run_surfaces)
            + len(session_level_reviews)
        )
        total_replay_count = len(session_replays)

        session_blockers = list(blocker_set)
        session_ready = (
            len(runs) > 0
            and verification_ready_runs == len(runs)
            and len(session_blockers) == 0
        )

        return {
            "session_id": session_id,
            "run_count": len(runs),
            "verification_ready_run_count": verification_ready_runs,
            "artifact_count": total_artifact_count,
            "review_count": total_review_count,
            "replay_count": total_replay_count,
            "latest_review_verdict": latest_review_verdict,
            "replay_equivalence_note": REPLAY_EQUIVALENCE_NOTE,
            "blockers": session_blockers,
            "verification_ready": session_ready,
            "runs": run_surfaces,
        }

    # ------------------------------------------------------------------
    # Public API — blocker explainer
    # ------------------------------------------------------------------

    @staticmethod
    def explain_blockers(surface_dict: Dict[str, Any]) -> List[str]:
        """Return a deterministic, human-readable list of blockers.

        The output preserves the order of the ``blockers`` field on
        the surface (which the service generates in a stable order:
        status, reviews, artifact hashes). Identical surface state
        always produces identical output, so callers can diff or
        snapshot it.
        """
        blockers = surface_dict.get("blockers", [])
        if not isinstance(blockers, list):
            return []
        return [str(b) for b in blockers]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _list_artifacts_for_run(
        self, harness_run_id: str
    ) -> List[Artifact]:
        """Return all artifacts linked to a run, ordered by creation.

        The :class:`ArtifactRepository` does not expose a
        ``list_by_harness_run`` query, so the service performs the
        SELECT directly. This is the only cross-entity read the
        service needs that no single repository owns.
        """
        rows = self.conn.execute(
            "SELECT artifact_id, workspace_id, producer_session_id, "
            "producer_harness_run_id, task_spec_id, artifact_kind, title, "
            "content_ref, content_hash, predecessor_artifact_id, created_at "
            "FROM artifacts WHERE producer_harness_run_id = ? "
            "ORDER BY created_at ASC",
            (harness_run_id,),
        ).fetchall()
        return [ArtifactRepository._row(r) for r in rows]

    def _list_reviews_for_run(
        self,
        run: HarnessRun,
        artifacts: List[Artifact],
    ) -> List[ReviewRecord]:
        """Return every review that targets the run, one of its
        artifacts, or its task spec.

        Reviews are deduplicated by ``review_id`` and ordered by
        ``created_at`` ascending so the "latest" verdict is
        deterministic.
        """
        seen: Dict[str, ReviewRecord] = {}

        # Reviews that directly target the run.
        for review in self._reviews.list_by_target("harness_run", run.harness_run_id):
            seen[review.review_id] = review

        # Reviews that target an artifact produced by the run.
        for artifact in artifacts:
            for review in self._reviews.list_by_target("artifact", artifact.artifact_id):
                seen[review.review_id] = review

        # Reviews that target the task spec the run is implementing.
        if run.task_spec_id:
            for review in self._reviews.list_by_target("task_spec", run.task_spec_id):
                seen[review.review_id] = review

        return sorted(seen.values(), key=lambda r: r.created_at)

    def _list_replays_for_run(
        self, harness_run_id: str
    ) -> List[ReplayRecord]:
        """Return every replay record that sources from this run."""
        rows = self.conn.execute(
            "SELECT replay_id, source_session_id, source_harness_run_id, "
            "fork_id, checkpoint_json, replay_scope, equivalence_rule, "
            "outcome, created_at "
            "FROM replay_records WHERE source_harness_run_id = ? "
            "ORDER BY created_at ASC",
            (harness_run_id,),
        ).fetchall()
        return [ReplayRecordRepository._row(r) for r in rows]

    @staticmethod
    def _compute_blockers(
        run: HarnessRun,
        artifacts: List[Artifact],
        reviews: List[ReviewRecord],
    ) -> List[str]:
        """Compute the blocker list for a single run.

        Blockers are produced in a fixed order so the surface is
        stable across calls: status, reviews, artifact hashes.
        """
        blockers: List[str] = []

        if run.status not in VERIFIABLE_RUN_STATUSES:
            blockers.append(
                f"Run status is {run.status!r}; verification requires one of "
                f"{sorted(VERIFIABLE_RUN_STATUSES)}."
            )

        if not reviews:
            blockers.append(
                "No review record exists for this run; verification requires "
                "at least one reviewer judgment."
            )

        unhashed = [a for a in artifacts if not a.content_hash]
        if unhashed:
            artifact_ids = ", ".join(sorted(a.artifact_id for a in unhashed))
            blockers.append(
                f"{len(unhashed)} artifact(s) linked to the run are missing "
                f"a content_hash and cannot be integrity-verified: {artifact_ids}."
            )

        return blockers


__all__ = [
    "VerificationService",
    "VERIFIABLE_RUN_STATUSES",
    "REPLAY_EQUIVALENCE_NOTE",
]
