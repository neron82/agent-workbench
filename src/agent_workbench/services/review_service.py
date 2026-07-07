"""ReviewService — review workflow hardening for the Agent Workbench.

This service composes :class:`ReviewRecordRepository` with the related
artifact/harness_run repositories to give Phase 7 a single, product-truth
surface for review creation, inspection, summarisation, and cross-harness
bundling.

Key invariants
--------------
* ReviewRecords remain append-only (delegated to the repository).
* ``summarize_review_state`` is the canonical product-layer "is this
  target blocked?" query — it returns ``blocking=True`` when the latest
  verdict for a target is ``fail`` or ``blocked``. ``conditional`` and
  ``pass`` are never blocking.
* ``build_review_bundle`` is a *product-truth* view: it queries the
  workbench database for related artifacts and harness runs. It does
  not invoke any runtime harness or external API.

Target kinds (mirrors the ``review_records.target_kind`` CHECK constraint):
    ``task_spec``, ``artifact``, ``harness_run``, ``session``.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from agent_workbench.models.artifact import ArtifactRepository
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.review_record import (
    ReviewRecord,
    ReviewRecordRepository,
)


# Recognised ``target_kind`` values for ReviewRecord — must stay aligned
# with the CHECK constraint declared in the ``review_records`` table.
TARGET_KIND_TASK_SPEC = "task_spec"
TARGET_KIND_ARTIFACT = "artifact"
TARGET_KIND_HARNESS_RUN = "harness_run"
TARGET_KIND_SESSION = "session"
TARGET_KINDS = (
    TARGET_KIND_TASK_SPEC,
    TARGET_KIND_ARTIFACT,
    TARGET_KIND_HARNESS_RUN,
    TARGET_KIND_SESSION,
)

# Recognised ``verdict`` values — must stay aligned with the CHECK
# constraint on ``review_records.verdict``.
VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_CONDITIONAL = "conditional"
VERDICT_BLOCKED = "blocked"
VERDICTS = (VERDICT_PASS, VERDICT_FAIL, VERDICT_CONDITIONAL, VERDICT_BLOCKED)

# Verdicts that make the target "blocking" (i.e. should halt downstream
# promotion or execution in the product workflow). ``conditional`` is
# intentionally NOT blocking — a conditional verdict asks the user to
# make a call rather than refuse to proceed.
_BLOCKING_VERDICTS = frozenset({VERDICT_FAIL, VERDICT_BLOCKED})


class ReviewServiceError(ValueError):
    """Raised when review inputs are invalid (e.g. unknown target_kind)."""


class ReviewService:
    """High-level review workflow service.

    The service is intentionally thin on top of the underlying
    repositories — it adds the *product-layer* concerns: validation,
    a single summarisation view, and cross-harness bundling.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.reviews = ReviewRecordRepository(conn)
        self.artifacts = ArtifactRepository(conn)
        self.harness_runs = HarnessRunRepository(conn)

    # ------------------------------------------------------------------
    # Public API: create / list / latest / summarise
    # ------------------------------------------------------------------

    def create_review(
        self,
        workspace_id: str,
        target_kind: str,
        target_id: str,
        verdict: str,
        findings_ref: Optional[str] = None,
        criteria_eval: Optional[Dict[str, Any]] = None,
        reviewer_binding_id: Optional[str] = None,
    ) -> ReviewRecord:
        """Append a new review record for a target.

        Returns the persisted :class:`ReviewRecord`. Reviews are
        append-only — this method never overwrites an earlier record.

        Raises
        ------
        ReviewServiceError
            If ``target_kind`` is not one of the recognised kinds, or
            ``verdict`` is not one of the allowed values, or
            ``workspace_id``/``target_id`` are empty.
        """
        if not workspace_id:
            raise ReviewServiceError("workspace_id must not be empty")
        if not target_id:
            raise ReviewServiceError("target_id must not be empty")
        if target_kind not in TARGET_KINDS:
            raise ReviewServiceError(
                f"Invalid target_kind: {target_kind!r}. "
                f"Must be one of {TARGET_KINDS}"
            )
        if verdict not in VERDICTS:
            raise ReviewServiceError(
                f"Invalid verdict: {verdict!r}. Must be one of {VERDICTS}"
            )

        return self.reviews.create(
            workspace_id=workspace_id,
            target_kind=target_kind,
            target_id=target_id,
            reviewer_binding_id=reviewer_binding_id,
            verdict=verdict,
            findings_ref=findings_ref,
            criteria_eval=criteria_eval,
        )

    def list_reviews(
        self, target_kind: str, target_id: str
    ) -> List[ReviewRecord]:
        """Return all review records for a target, oldest first.

        The ordering is provided by the repository (``ORDER BY
        created_at ASC``) so callers can rely on insertion order.
        """
        if target_kind not in TARGET_KINDS:
            raise ReviewServiceError(
                f"Invalid target_kind: {target_kind!r}. "
                f"Must be one of {TARGET_KINDS}"
            )
        if not target_id:
            raise ReviewServiceError("target_id must not be empty")
        return self.reviews.list_by_target(target_kind, target_id)

    def latest_review(
        self, target_kind: str, target_id: str
    ) -> Optional[ReviewRecord]:
        """Return the most recent review for a target, or ``None``.

        "Most recent" is defined as the last row returned by
        :meth:`list_reviews` (i.e. the highest ``created_at``).
        """
        reviews = self.list_reviews(target_kind, target_id)
        return reviews[-1] if reviews else None

    def summarize_review_state(
        self, target_kind: str, target_id: str
    ) -> Dict[str, Any]:
        """Summarise the review state of a target.

        Returns a dict with the following keys:

        * ``target_kind`` — echoed from the input.
        * ``target_id`` — echoed from the input.
        * ``review_count`` — number of review rows for this target.
        * ``latest_verdict`` — the verdict of the most recent review,
          or ``None`` if no reviews exist.
        * ``latest_review_id`` — id of the most recent review, or
          ``None``.
        * ``latest_reviewed_at`` — Unix timestamp of the most recent
          review, or ``None``.
        * ``blocking`` — ``True`` iff ``latest_verdict`` is one of
          ``fail`` / ``blocked`` and at least one review exists.
        """
        latest = self.latest_review(target_kind, target_id)
        count = len(self.list_reviews(target_kind, target_id))
        latest_verdict = latest.verdict if latest is not None else None
        blocking = (
            latest_verdict in _BLOCKING_VERDICTS if latest is not None else False
        )
        return {
            "target_kind": target_kind,
            "target_id": target_id,
            "review_count": count,
            "latest_verdict": latest_verdict,
            "latest_review_id": latest.review_id if latest is not None else None,
            "latest_reviewed_at": latest.created_at if latest is not None else None,
            "blocking": blocking,
        }

    # ------------------------------------------------------------------
    # Cross-harness review bundle
    # ------------------------------------------------------------------

    def build_review_bundle(
        self, target_kind: str, target_id: str
    ) -> Dict[str, Any]:
        """Build a product-truth bundle for a review target.

        The bundle always includes the review history and the summary
        view produced by :meth:`summarize_review_state`. It then
        enriches the bundle with related records where inferable from
        the product schema:

        * ``target_kind == "artifact"`` — the artifact row plus any
          harness run referenced via ``producer_harness_run_id``.
        * ``target_kind == "harness_run"`` — the harness run row plus
          any artifacts produced by that run.
        * ``target_kind == "session"`` — all harness runs and all
          artifacts for that session.
        * ``target_kind == "task_spec"`` — no inferred related rows
          (task_spec_id is referenced by artifacts / harness_runs but
          not by reviews; the bundle still carries the review history).

        All lookups are pure SQLite reads against ``workbench.db`` —
        no runtime harness, no external API.
        """
        reviews = self.list_reviews(target_kind, target_id)
        summary = self.summarize_review_state(target_kind, target_id)

        bundle: Dict[str, Any] = {
            "target_kind": target_kind,
            "target_id": target_id,
            "summary": summary,
            "reviews": [self._review_to_dict(r) for r in reviews],
            "related": self._collect_related(target_kind, target_id),
        }
        return bundle

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_related(
        self, target_kind: str, target_id: str
    ) -> Dict[str, Any]:
        """Return related artifacts/harness_runs for the given target.

        Returns a dict with the keys ``artifacts`` and ``harness_runs``
        (lists of matching rows, possibly empty). Missing records are
        represented by ``None`` for that particular relation rather
        than by raising — a review bundle should be renderable even
        when the referenced target has been deleted.
        """
        related: Dict[str, Any] = {
            "artifact": None,
            "harness_run": None,
            "artifacts": [],
            "harness_runs": [],
        }

        if target_kind == TARGET_KIND_ARTIFACT:
            artifact = self.artifacts.get_by_id(target_id)
            related["artifact"] = (
                self._artifact_to_dict(artifact) if artifact is not None else None
            )
            if artifact is not None and artifact.producer_harness_run_id:
                run = self.harness_runs.get_by_id(artifact.producer_harness_run_id)
                related["harness_run"] = (
                    self._harness_run_to_dict(run) if run is not None else None
                )

        elif target_kind == TARGET_KIND_HARNESS_RUN:
            run = self.harness_runs.get_by_id(target_id)
            related["harness_run"] = (
                self._harness_run_to_dict(run) if run is not None else None
            )
            if run is not None:
                related["artifacts"] = [
                    self._artifact_to_dict(a)
                    for a in self._list_artifacts_for_run(target_id)
                ]

        elif target_kind == TARGET_KIND_SESSION:
            related["harness_runs"] = [
                self._harness_run_to_dict(r)
                for r in self.harness_runs.list_by_session(target_id)
            ]
            related["artifacts"] = [
                self._artifact_to_dict(a)
                for a in self.artifacts.list_by_session(target_id)
            ]

        return related

    def _list_artifacts_for_run(self, harness_run_id: str) -> List[Any]:
        """Return artifacts whose ``producer_harness_run_id`` matches.

        Done as a direct SQL read — the artifact repository exposes
        ``list_by_session`` and ``list_by_task_spec`` but not
        ``list_by_harness_run``, so we keep the bundle helper's needs
        local to the service.
        """
        rows = self.conn.execute(
            "SELECT artifact_id, workspace_id, producer_session_id, "
            "producer_harness_run_id, task_spec_id, artifact_kind, title, "
            "content_ref, content_hash, predecessor_artifact_id, created_at "
            "FROM artifacts WHERE producer_harness_run_id = ? "
            "ORDER BY created_at ASC",
            (harness_run_id,),
        ).fetchall()
        # Reuse the repository's _row helper so we get a proper Artifact
        # dataclass (it only needs the row's column names — it does not
        # care whether the row was fetched via the repo or directly).
        from agent_workbench.models.artifact import ArtifactRepository

        return [ArtifactRepository._row(r) for r in rows]

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _review_to_dict(r: ReviewRecord) -> Dict[str, Any]:
        return {
            "review_id": r.review_id,
            "workspace_id": r.workspace_id,
            "target_kind": r.target_kind,
            "target_id": r.target_id,
            "reviewer_binding_id": r.reviewer_binding_id,
            "verdict": r.verdict,
            "findings_ref": r.findings_ref,
            "criteria_eval": r.criteria_eval,
            "created_at": r.created_at,
        }

    @staticmethod
    def _artifact_to_dict(a: Any) -> Dict[str, Any]:
        return {
            "artifact_id": a.artifact_id,
            "workspace_id": a.workspace_id,
            "producer_session_id": a.producer_session_id,
            "producer_harness_run_id": a.producer_harness_run_id,
            "task_spec_id": a.task_spec_id,
            "artifact_kind": a.artifact_kind,
            "title": a.title,
            "content_ref": a.content_ref,
            "content_hash": a.content_hash,
            "predecessor_artifact_id": a.predecessor_artifact_id,
            "created_at": a.created_at,
        }

    @staticmethod
    def _harness_run_to_dict(r: Any) -> Dict[str, Any]:
        return {
            "harness_run_id": r.harness_run_id,
            "workspace_id": r.workspace_id,
            "session_id": r.session_id,
            "task_spec_id": r.task_spec_id,
            "harness_type": r.harness_type,
            "runtime_session_id": r.runtime_session_id,
            "runtime_process_id": r.runtime_process_id,
            "runtime_remote_process_id": r.runtime_remote_process_id,
            "status": r.status,
            "control_capabilities_json": r.control_capabilities_json,
            "artifact_summary_json": r.artifact_summary_json,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
        }


__all__ = [
    "ReviewService",
    "ReviewServiceError",
    "TARGET_KINDS",
    "VERDICTS",
    "TARGET_KIND_TASK_SPEC",
    "TARGET_KIND_ARTIFACT",
    "TARGET_KIND_HARNESS_RUN",
    "TARGET_KIND_SESSION",
    "VERDICT_PASS",
    "VERDICT_FAIL",
    "VERDICT_CONDITIONAL",
    "VERDICT_BLOCKED",
]
