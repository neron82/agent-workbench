"""ReplayService — trustworthy replay semantics for the Agent Workbench.

This service implements the product-layer replay contract defined in
``03_DOMAIN_MODEL.md`` and the Phase 7 hardening spec (10_ORCHESTRATOR_CONTRACT_PHASES_3_9.md).

Key responsibilities
--------------------
* Normalize replay checkpoints into a versioned, session-scoped shape
  (``version``, ``source_session_id``, ``source_message_offset``) and
  preserve any extra caller-supplied fields.
* Create :class:`ReplayRecord` rows with the fixed
  ``equivalence_rule = 'final_state_plus_reviewer_judgment'`` value, and
  a validated ``outcome`` from the schema's three-way enum.
* List replays for a given source harness run.
* Compute replay equivalence on **final-state signals only**
  (artifact content hashes, artifact count, reviewer verdict) — never
  on raw tool-call sequence equality. The spec is explicit:

  > "replay equivalence is outcome-based, not exact-call-sequence-based"

  (03_DOMAIN_MODEL.md §6, MVP modeling decisions carried from
  ``open_decisions.md``).

* Provide a session-scoped replay timeline view.

Design notes
------------
* Replay records live in the product-layer ``replay_records`` table
  inside ``workbench.db``. They are **not** stored in Hermes-only
  tables.
* The service delegates raw persistence to
  :class:`ReplayRecordRepository` and adds the product-layer
  validation, normalization, and equivalence policy that the
  repository alone cannot enforce.
* All hash comparison is set-based: ordering and multiplicity are
  not part of the equivalence signal. This is intentional — a replay
  that produces the same final artifacts in a different order is
  still equivalent for product purposes.
* Stdlib only. No third-party dependencies.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from agent_workbench.models.replay_record import (
    ReplayRecord,
    ReplayRecordRepository,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Current checkpoint envelope version. Bump when the shape changes
#: incompatibly. Existing checkpoints with older ``version`` values are
#: still accepted by :meth:`ReplayService.normalize_checkpoint` — the
#: version is preserved on the returned dict so downstream readers can
#: dispatch on it.
_CHECKPOINT_VERSION = 1

#: Default offset when none is supplied.
_DEFAULT_SOURCE_MESSAGE_OFFSET = 0

#: Fixed equivalence rule for the Phase 7 trust model. Per
#: 03_DOMAIN_MODEL.md §2, the ``ReplayRecord.equivalence_rule`` column
#: is constrained to this single value.
EQUIVALENCE_RULE = "final_state_plus_reviewer_judgment"

#: Validated outcome values, mirroring the schema's CHECK constraint.
_VALID_OUTCOMES = ("completed", "diverged", "aborted")

#: Reviewer verdicts that defeat an otherwise-matching final state.
#: ``fail`` and ``blocked`` both mean "this replay is NOT equivalent
#: even if the artifacts look the same". ``pass`` and ``conditional``
#: do not override the hash comparison.
_FAILING_VERDICTS = ("fail", "blocked")


class ReplayService:
    """Service for replay record creation, normalization, and equivalence.

    The service wraps :class:`ReplayRecordRepository` and adds the
    product-layer validation, checkpoint normalization, and
    outcome-based equivalence policy that the repository alone cannot
    enforce.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.replay_repo = ReplayRecordRepository(conn)

    # ------------------------------------------------------------------
    # Public API — replay lifecycle
    # ------------------------------------------------------------------

    def normalize_checkpoint(
        self,
        checkpoint: Optional[Dict[str, Any]],
        *,
        source_session_id: str,
        source_message_offset: int = _DEFAULT_SOURCE_MESSAGE_OFFSET,
    ) -> Dict[str, Any]:
        """Return a versioned checkpoint dict for ``source_session_id``.

        The returned dict always contains three core keys:

        * ``version`` — the checkpoint envelope version (int).
        * ``source_session_id`` — the session this checkpoint anchors to.
        * ``source_message_offset`` — the message index within that
          session this checkpoint was taken at.

        Any extra keys supplied on the input are preserved verbatim so
        the caller's payload survives the normalization step. Extra
        keys do **not** override the three core keys — those are
        always derived from the explicit function arguments.

        Parameters
        ----------
        checkpoint:
            Either ``None`` (in which case a minimal versioned
            envelope is constructed) or a dict-like object. Any other
            type raises :class:`ValueError`.
        source_session_id:
            The session id stamped onto the returned checkpoint.
        source_message_offset:
            The message offset stamped onto the returned checkpoint.
            Defaults to 0.

        Returns
        -------
        dict
            A new dict with at minimum the three core keys. Callers
            may freely mutate the returned dict — the service does not
            retain a reference.

        Raises
        ------
        ValueError
            If ``checkpoint`` is not ``None`` and not a ``dict``, or
            if ``source_session_id`` is empty.
        """
        if not source_session_id:
            raise ValueError("source_session_id must not be empty")

        if checkpoint is None:
            base: Dict[str, Any] = {}
        elif isinstance(checkpoint, dict):
            # Shallow-copy so we never mutate the caller's dict.
            base = dict(checkpoint)
        else:
            raise ValueError(
                f"checkpoint must be a dict or None, got {type(checkpoint).__name__}"
            )

        # Core keys are always derived from the explicit arguments;
        # they win over any colliding extra keys from the input.
        base["version"] = _CHECKPOINT_VERSION
        base["source_session_id"] = source_session_id
        base["source_message_offset"] = source_message_offset

        return base

    def create_replay(
        self,
        source_session_id: str,
        source_harness_run_id: Optional[str],
        fork_id: str,
        checkpoint: Optional[Dict[str, Any]] = None,
        replay_scope: str = "",
        outcome: str = "completed",
    ) -> ReplayRecord:
        """Create a :class:`ReplayRecord` with a normalized checkpoint.

        The checkpoint is run through :meth:`normalize_checkpoint`
        before being handed to the repository. The
        ``equivalence_rule`` is always set to
        :data:`EQUIVALENCE_RULE` and cannot be overridden — the spec
        fixes this for the Phase 7 trust model.

        Parameters
        ----------
        source_session_id:
            The session being replayed from.
        source_harness_run_id:
            Optional id of the source harness run the replay is
            replaying from. ``None`` means the replay is anchored
            only to the session (no run-scoped artefacts).
        fork_id:
            The id of the :class:`ForkRecord` that produced this
            replay. Must reference a row in ``fork_records``.
        checkpoint:
            Optional caller-supplied checkpoint payload. Forwarded to
            :meth:`normalize_checkpoint`. Extra keys are preserved.
        replay_scope:
            Free-text scope label (e.g. ``"full"``, ``"from-step-5"``).
        outcome:
            One of ``"completed"``, ``"diverged"``, ``"aborted"``.
            Defaults to ``"completed"``.

        Returns
        -------
        ReplayRecord
            The persisted replay record.

        Raises
        ------
        ValueError
            If ``source_session_id`` is empty, ``fork_id`` is empty,
            ``outcome`` is not in the validated set, or ``checkpoint``
            fails normalization.
        """
        if not source_session_id:
            raise ValueError("source_session_id must not be empty")
        if not fork_id:
            raise ValueError("fork_id must not be empty")
        if outcome not in _VALID_OUTCOMES:
            raise ValueError(
                f"Invalid outcome: {outcome!r}. "
                f"Must be one of {_VALID_OUTCOMES}"
            )

        normalized = self.normalize_checkpoint(
            checkpoint,
            source_session_id=source_session_id,
        )

        return self.replay_repo.create(
            source_session_id=source_session_id,
            source_harness_run_id=source_harness_run_id,
            fork_id=fork_id,
            checkpoint=normalized,
            replay_scope=replay_scope,
            equivalence_rule=EQUIVALENCE_RULE,
            outcome=outcome,
        )

    def list_replays_for_run(
        self, harness_run_id: str
    ) -> List[ReplayRecord]:
        """Return all replays whose ``source_harness_run_id`` matches.

        Replays that have no source run (``source_harness_run_id IS
        NULL``) are **not** returned here — they are session-scoped
        only and belong in :meth:`get_replay_timeline`.

        Parameters
        ----------
        harness_run_id:
            The harness run id to filter on.

        Returns
        -------
        list[ReplayRecord]
            A list of matching replay records, ordered by
            ``created_at`` ascending (oldest first). Empty when no
            replay has been recorded for the run.
        """
        if not harness_run_id:
            return []

        rows = self.conn.execute(
            "SELECT replay_id, source_session_id, source_harness_run_id, "
            "fork_id, checkpoint_json, replay_scope, equivalence_rule, "
            "outcome, created_at "
            "FROM replay_records "
            "WHERE source_harness_run_id = ? "
            "ORDER BY created_at ASC",
            (harness_run_id,),
        ).fetchall()
        return [ReplayRecordRepository._row(r) for r in rows]

    def get_replay_timeline(
        self, source_session_id: str
    ) -> List[ReplayRecord]:
        """Return all replays anchored to ``source_session_id``.

        This is the session-scoped timeline view, useful for
        operator-facing replay history.

        Parameters
        ----------
        source_session_id:
            The session id to filter on.

        Returns
        -------
        list[ReplayRecord]
            A list of replay records, ordered by ``created_at``
            ascending. Empty when the session has no replays.
        """
        if not source_session_id:
            return []
        return list(self.replay_repo.list_by_session(source_session_id))

    # ------------------------------------------------------------------
    # Public API — equivalence evaluation
    # ------------------------------------------------------------------

    def evaluate_equivalence(
        self,
        *,
        source_harness_run_id: str,
        candidate_harness_run_id: Optional[str] = None,
        reviewer_verdict: Optional[str] = None,
        source_artifact_ids: Optional[List[str]] = None,
        candidate_artifact_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Evaluate whether a candidate run is equivalent to a source run.

        Equivalence is **outcome-based**, never exact-call-sequence.
        The only signals consulted are:

        1. Reviewer verdict (when provided).
        2. Artifact content hashes (when provided) and the
           corresponding artifact counts.
        3. The presence of evidence on both sides — missing evidence
           yields an explicit, non-equivalent result.

        Parameters
        ----------
        source_harness_run_id:
            The harness run the candidate is being compared against.
            Required so that the result can be correlated to a known
            reference run.
        candidate_harness_run_id:
            Optional id of the candidate run. Carried for symmetry and
            for callers that want to embed it in their own bookkeeping;
            the equivalence policy itself does not read it.
        reviewer_verdict:
            Optional reviewer verdict (``"pass"``, ``"fail"``,
            ``"conditional"``, ``"blocked"``). When ``"fail"`` or
            ``"blocked"`` the result is forced to
            ``equivalent = False`` regardless of the artifact hashes.
        source_artifact_ids:
            Optional list of artifact ids from the source run. Each
            id is resolved to its ``content_hash`` via
            :class:`ArtifactRepository`. ``None`` entries or ids that
            cannot be resolved contribute no hash.
        candidate_artifact_ids:
            Optional list of artifact ids from the candidate run.
            Same resolution rules as ``source_artifact_ids``.

        Returns
        -------
        dict
            Structured result with keys:

            * ``equivalent`` (bool) — whether the two runs are
              equivalent under the outcome-based rule.
            * ``rule`` (str) — the name of the rule applied, always
              :data:`EQUIVALENCE_RULE`.
            * ``reasons`` (list[str]) — human-readable explanation
              lines. Always non-empty so callers can render the
              result without a separate code path.
            * ``source_artifact_hashes`` (list[str]) — the resolved
              hashes for the source side, in input order with
              duplicates and ``None`` artifacts preserved.
            * ``candidate_artifact_hashes`` (list[str]) — the resolved
              hashes for the candidate side.
            * ``source_harness_run_id`` (str) — echoed from input.
            * ``candidate_harness_run_id`` (str or None) — echoed
              from input.
        """
        if not source_harness_run_id:
            raise ValueError("source_harness_run_id must not be empty")

        source_hashes = self._resolve_artifact_hashes(source_artifact_ids)
        candidate_hashes = self._resolve_artifact_hashes(candidate_artifact_ids)

        reasons: List[str] = []
        equivalent = True

        # Reviewer verdict first — it overrides everything else.
        if reviewer_verdict is not None:
            normalized_verdict = reviewer_verdict.strip().lower()
            if normalized_verdict in _FAILING_VERDICTS:
                equivalent = False
                reasons.append(
                    f"reviewer verdict {reviewer_verdict!r} overrides "
                    f"final-state match (fail/blocked verdicts cannot "
                    f"be reconciled by hash equality)"
                )
            else:
                reasons.append(
                    f"reviewer verdict {reviewer_verdict!r} is not "
                    f"fail/blocked and does not force divergence"
                )

        # Then artifact hash comparison — outcome-based, set-based.
        has_source = len(source_hashes) > 0
        has_candidate = len(candidate_hashes) > 0

        if not has_source and not has_candidate:
            equivalent = False
            reasons.append(
                "no artifact evidence on either side — cannot establish "
                "final-state equivalence"
            )
        elif not has_source:
            equivalent = False
            reasons.append(
                "source artifact evidence is missing — cannot establish "
                "final-state equivalence"
            )
        elif not has_candidate:
            equivalent = False
            reasons.append(
                "candidate artifact evidence is missing — cannot establish "
                "final-state equivalence"
            )
        else:
            source_set = set(source_hashes)
            candidate_set = set(candidate_hashes)
            source_count = len(source_hashes)
            candidate_count = len(candidate_hashes)
            if source_set == candidate_set and source_count == candidate_count:
                # Both set equality and multiplicity match. The hashes
                # agree on the final state.
                reasons.append(
                    f"artifact content hashes match exactly "
                    f"({source_count} artifact(s) on each side)"
                )
            else:
                equivalent = False
                reasons.append(
                    f"artifact content hashes differ: "
                    f"source={sorted(source_set)}, "
                    f"candidate={sorted(candidate_set)}; "
                    f"counts source={source_count}, candidate={candidate_count}"
                )

        return {
            "equivalent": equivalent,
            "rule": EQUIVALENCE_RULE,
            "reasons": reasons,
            "source_artifact_hashes": list(source_hashes),
            "candidate_artifact_hashes": list(candidate_hashes),
            "source_harness_run_id": source_harness_run_id,
            "candidate_harness_run_id": candidate_harness_run_id,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_artifact_hashes(
        self, artifact_ids: Optional[List[str]]
    ) -> List[str]:
        """Resolve a list of artifact ids to their ``content_hash`` values.

        * ``None`` input → empty list.
        * Unresolvable ids are skipped (they cannot contribute to a
          final-state comparison).
        * Artifacts whose ``content_hash`` is ``NULL`` are skipped —
          a missing hash is not evidence of equivalence.
        """
        if not artifact_ids:
            return []

        # De-duplicate while preserving the caller's order; we still
        # treat the result as a list below so callers see what they
        # asked for. Multiplicity matters in addition to set
        # membership (e.g. an artifact produced twice means something
        # different from one produced once), so we keep the
        # multi-set semantics in the result.
        resolved: List[str] = []
        for artifact_id in artifact_ids:
            if not artifact_id:
                continue
            row = self.conn.execute(
                "SELECT content_hash FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                continue
            content_hash = row["content_hash"]
            if not content_hash:
                continue
            resolved.append(content_hash)
        return resolved


__all__ = [
    "ReplayService",
    "EQUIVALENCE_RULE",
]
