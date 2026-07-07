"""ArtifactVerifier — product-layer artifact integrity verification.

The MVP integrity check is intentionally simple:

* The artifact row stores an *expected* ``content_hash`` (recorded at
  creation time by the producer).
* The verifier computes a *current* hash from the artifact's
  ``content_ref`` using stdlib :mod:`hashlib` (sha256 of the literal
  string bytes).
* If both hashes are present and equal, the artifact is considered
  valid. Any missing hash, missing row, or hash mismatch makes the
  artifact invalid.

This is a *product-layer* integrity check — it verifies that what the
product wrote as the artifact's identifier still corresponds to the
current content reference, not that the underlying blob on disk is
unchanged. Phase 8 may extend this to fetch the blob and hash it
byte-for-byte; the public API is shaped so that change is a drop-in.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Dict, List, Optional

from agent_workbench.models.artifact import Artifact, ArtifactRepository


class ArtifactVerifier:
    """SQLite-backed artifact integrity verifier."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.artifacts = ArtifactRepository(conn)

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------

    @staticmethod
    def compute_content_hash(content_ref: Optional[str]) -> Optional[str]:
        """Compute the sha256 of the literal ``content_ref`` string bytes.

        Returns ``None`` when ``content_ref`` is ``None`` or the empty
        string — there is no content to hash. This matches the
        "no content -> no claim" semantics that the rest of the
        product uses for nullable ``content_ref``/``content_hash``
        columns.
        """
        if content_ref is None:
            return None
        if content_ref == "":
            return None
        return hashlib.sha256(content_ref.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Single-artifact verification
    # ------------------------------------------------------------------

    def verify_artifact(self, artifact_id: str) -> Dict[str, Any]:
        """Verify a single artifact by id.

        Returns a dict with the following keys:

        * ``artifact_id`` — the id passed in.
        * ``expected_hash`` — the value of ``artifacts.content_hash``,
          or ``None`` if the row is missing or the column is NULL.
        * ``actual_hash`` — the freshly-computed hash of
          ``artifacts.content_ref``, or ``None`` if ``content_ref`` is
          NULL/empty.
        * ``valid`` — ``True`` iff the artifact exists, both hashes
          are present, and they match.
        * ``reason`` — a short human-readable explanation of the
          outcome (e.g. ``"ok"``, ``"artifact_missing"``,
          ``"expected_hash_missing"``, ``"actual_hash_missing"``,
          ``"hash_mismatch"``).
        """
        artifact = self.artifacts.get_by_id(artifact_id)

        if artifact is None:
            return {
                "artifact_id": artifact_id,
                "expected_hash": None,
                "actual_hash": None,
                "valid": False,
                "reason": "artifact_missing",
            }

        expected_hash = artifact.content_hash
        actual_hash = self.compute_content_hash(artifact.content_ref)

        if expected_hash is None:
            return self._result(artifact, expected_hash, actual_hash, False, "expected_hash_missing")
        if actual_hash is None:
            return self._result(artifact, expected_hash, actual_hash, False, "actual_hash_missing")
        if expected_hash != actual_hash:
            return self._result(artifact, expected_hash, actual_hash, False, "hash_mismatch")
        return self._result(artifact, expected_hash, actual_hash, True, "ok")

    # ------------------------------------------------------------------
    # Per-run aggregation
    # ------------------------------------------------------------------

    def verify_artifacts_for_run(
        self, harness_run_id: str
    ) -> Dict[str, Any]:
        """Verify every artifact produced by a given harness run.

        Returns a dict with the following keys:

        * ``harness_run_id`` — the id passed in.
        * ``checked_count`` — number of artifacts inspected (zero if
          the run produced none).
        * ``all_valid`` — ``True`` iff every inspected artifact passed
          verification. If the run produced no artifacts, ``all_valid``
          is ``True`` (vacuously).
        * ``invalid_artifact_ids`` — list of artifact ids whose
          verification returned ``valid=False``. May be empty.
        * ``results`` — list of per-artifact result dicts, one per
          inspected artifact, in insertion order (oldest first).
        """
        rows = self.conn.execute(
            "SELECT artifact_id FROM artifacts "
            "WHERE producer_harness_run_id = ? ORDER BY created_at ASC",
            (harness_run_id,),
        ).fetchall()

        results: List[Dict[str, Any]] = []
        invalid: List[str] = []
        for row in rows:
            artifact_id = row["artifact_id"]
            result = self.verify_artifact(artifact_id)
            results.append(result)
            if not result["valid"]:
                invalid.append(artifact_id)

        return {
            "harness_run_id": harness_run_id,
            "checked_count": len(results),
            "all_valid": len(invalid) == 0,
            "invalid_artifact_ids": invalid,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _result(
        artifact: Artifact,
        expected_hash: Optional[str],
        actual_hash: Optional[str],
        valid: bool,
        reason: str,
    ) -> Dict[str, Any]:
        return {
            "artifact_id": artifact.artifact_id,
            "expected_hash": expected_hash,
            "actual_hash": actual_hash,
            "valid": valid,
            "reason": reason,
        }


__all__ = ["ArtifactVerifier"]
