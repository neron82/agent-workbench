"""Tests for ReplayService — trustworthy replay semantics (Phase 7).

These tests cover the Phase 7 replay service:
* ``normalize_checkpoint`` returns a versioned, session-scoped dict
  that always carries the three core keys, preserves caller-supplied
  extras, defaults to a v1 envelope on ``None``, and rejects non-dict
  input.
* ``create_replay`` persists the **normalized** checkpoint, pins
  ``equivalence_rule`` to the spec-fixed value, and validates
  ``outcome``.
* ``evaluate_equivalence`` is outcome-based (artifact hashes +
  reviewer verdict only), not exact-call-sequence based; a
  ``fail``/``blocked`` verdict overrides matching hashes; missing
  evidence on either side yields a non-equivalent result with an
  explicit reason.
* ``list_replays_for_run`` filters correctly on
  ``source_harness_run_id``.
* ``get_replay_timeline`` returns session-scoped replays in order.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from agent_workbench.models.artifact import ArtifactRepository
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.replay_record import ReplayRecord
from agent_workbench.services.replay_service import (
    EQUIVALENCE_RULE,
    ReplayService,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def replay_service(db: sqlite3.Connection) -> ReplayService:
    return ReplayService(db)


@pytest.fixture
def artifact_repo(db: sqlite3.Connection) -> ArtifactRepository:
    return ArtifactRepository(db)


@pytest.fixture
def harness_run_repo(db: sqlite3.Connection) -> HarnessRunRepository:
    return HarnessRunRepository(db)


def _seed_fork(db: sqlite3.Connection) -> str:
    """Insert a minimal fork record so replay_records FK is satisfied."""
    fork_id = uuid.uuid4().hex
    db.execute(
        "INSERT INTO fork_records "
        "(fork_id, parent_session_id, child_session_id, fork_kind, "
        "fork_reason, initiated_by, bootstrap_context_role_internal, created_at) "
        "VALUES (?, ?, ?, 'replay', '', 'orchestrator', 'fork_context', 0)",
        (fork_id, "parent-sess", "child-sess"),
    )
    db.commit()
    return fork_id


def _seed_harness_run(
    db: sqlite3.Connection, workspace_id: str, session_id: str = "run-sess"
) -> str:
    """Insert a real harness_runs row so FK from replay_records is satisfied."""
    run = HarnessRunRepository(db).create(
        workspace_id=workspace_id,
        session_id=session_id,
        harness_type="discussion",
    )
    return run.harness_run_id


def _seed_workspace(db: sqlite3.Connection) -> str:
    """Insert a minimal workspace so artifacts FK is satisfied."""
    wid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO workspaces (workspace_id, tenant_id, name, is_default, created_at) "
        "VALUES (?, ?, ?, 0, 0)",
        (wid, "tenant-1", "replay-test-ws"),
    )
    db.commit()
    return wid


def _make_artifact(
    artifact_repo: ArtifactRepository,
    workspace_id: str,
    *,
    content_hash: str,
    session_id: str = "producer-sess",
) -> str:
    """Create an artifact and return its id."""
    art = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id=session_id,
        artifact_kind="code",
        title=f"artifact-{content_hash[:6]}",
        content_hash=content_hash,
    )
    return art.artifact_id


# ---------------------------------------------------------------------------
# normalize_checkpoint
# ---------------------------------------------------------------------------


class TestNormalizeCheckpoint:
    def test_none_input_creates_v1_envelope(
        self, replay_service: ReplayService
    ) -> None:
        """``None`` becomes a minimal versioned envelope."""
        cp = replay_service.normalize_checkpoint(
            None, source_session_id="sess-A"
        )
        assert cp == {
            "version": 1,
            "source_session_id": "sess-A",
            "source_message_offset": 0,
        }

    def test_dict_input_preserves_extras(
        self, replay_service: ReplayService
    ) -> None:
        """Extra keys survive; core keys are derived from arguments."""
        cp = replay_service.normalize_checkpoint(
            {
                "token_position": 42,
                "prompt_hash": "ph-1",
                "artifact_refs": ["a", "b"],
            },
            source_session_id="sess-B",
            source_message_offset=7,
        )
        assert cp["version"] == 1
        assert cp["source_session_id"] == "sess-B"
        assert cp["source_message_offset"] == 7
        assert cp["token_position"] == 42
        assert cp["prompt_hash"] == "ph-1"
        assert cp["artifact_refs"] == ["a", "b"]

    def test_core_keys_override_collisions(
        self, replay_service: ReplayService
    ) -> None:
        """Even if a caller tries to spoof the core keys, arguments win."""
        cp = replay_service.normalize_checkpoint(
            {
                "version": 999,
                "source_session_id": "spoofed",
                "source_message_offset": -1,
                "extra": "kept",
            },
            source_session_id="real",
            source_message_offset=12,
        )
        assert cp["version"] == 1
        assert cp["source_session_id"] == "real"
        assert cp["source_message_offset"] == 12
        assert cp["extra"] == "kept"

    def test_does_not_mutate_caller_dict(
        self, replay_service: ReplayService
    ) -> None:
        """The caller's input must not be modified in place."""
        original = {"a": 1, "b": 2}
        snapshot = dict(original)
        cp = replay_service.normalize_checkpoint(
            original, source_session_id="sess-X"
        )
        assert original == snapshot
        # Returned dict has the core keys but the original did not.
        assert "version" not in original
        assert cp["version"] == 1

    def test_rejects_non_dict(self, replay_service: ReplayService) -> None:
        """Non-dict checkpoints raise :class:`ValueError`."""
        for bad in ([], "string", 42, 3.14, ("a", "b"), object()):
            with pytest.raises(ValueError):
                replay_service.normalize_checkpoint(
                    bad,  # type: ignore[arg-type]
                    source_session_id="sess-X",
                )

    def test_rejects_empty_source_session_id(
        self, replay_service: ReplayService
    ) -> None:
        with pytest.raises(ValueError):
            replay_service.normalize_checkpoint(
                {"a": 1}, source_session_id=""
            )

    def test_returns_new_dict_each_call(
        self, replay_service: ReplayService
    ) -> None:
        """Two calls with the same input return independent dicts."""
        a = replay_service.normalize_checkpoint(
            {"x": 1}, source_session_id="sess-A"
        )
        b = replay_service.normalize_checkpoint(
            {"x": 1}, source_session_id="sess-A"
        )
        assert a == b
        assert a is not b
        a["mutated"] = True
        assert "mutated" not in b


# ---------------------------------------------------------------------------
# create_replay
# ---------------------------------------------------------------------------


class TestCreateReplay:
    def test_persists_normalized_checkpoint(
        self, replay_service: ReplayService
    ) -> None:
        """Checkpoint is normalized before being handed to the repo."""
        fork_id = _seed_fork(replay_service.conn)
        rec = replay_service.create_replay(
            source_session_id="sess-1",
            source_harness_run_id=None,
            fork_id=fork_id,
            checkpoint={"state": "ready", "step": 5},
            replay_scope="from-step-5",
            outcome="completed",
        )
        assert isinstance(rec, ReplayRecord)
        assert rec.fork_id == fork_id
        assert rec.source_session_id == "sess-1"
        assert rec.source_harness_run_id is None
        assert rec.replay_scope == "from-step-5"
        assert rec.outcome == "completed"
        # Normalized envelope is the persisted value.
        assert rec.checkpoint is not None
        assert rec.checkpoint["version"] == 1
        assert rec.checkpoint["source_session_id"] == "sess-1"
        assert rec.checkpoint["source_message_offset"] == 0
        assert rec.checkpoint["state"] == "ready"
        assert rec.checkpoint["step"] == 5

    def test_none_checkpoint_yields_v1_envelope(
        self, replay_service: ReplayService
    ) -> None:
        fork_id = _seed_fork(replay_service.conn)
        rec = replay_service.create_replay(
            source_session_id="sess-2",
            source_harness_run_id=None,
            fork_id=fork_id,
        )
        assert rec.checkpoint == {
            "version": 1,
            "source_session_id": "sess-2",
            "source_message_offset": 0,
        }

    def test_equivalence_rule_is_fixed(
        self, replay_service: ReplayService
    ) -> None:
        """``equivalence_rule`` is always the spec-fixed value."""
        fork_id = _seed_fork(replay_service.conn)
        rec = replay_service.create_replay(
            source_session_id="sess-3",
            source_harness_run_id=None,
            fork_id=fork_id,
            outcome="diverged",
        )
        assert rec.equivalence_rule == EQUIVALENCE_RULE
        assert rec.equivalence_rule == "final_state_plus_reviewer_judgment"

    def test_invalid_outcome_rejected(
        self, replay_service: ReplayService
    ) -> None:
        fork_id = _seed_fork(replay_service.conn)
        with pytest.raises(ValueError):
            replay_service.create_replay(
                source_session_id="sess-4",
                source_harness_run_id=None,
                fork_id=fork_id,
                outcome="bogus",
            )

    def test_each_outcome_value_accepted(
        self, replay_service: ReplayService
    ) -> None:
        for outcome in ("completed", "diverged", "aborted"):
            fork_id = _seed_fork(replay_service.conn)
            rec = replay_service.create_replay(
                source_session_id="sess-out",
                source_harness_run_id=None,
                fork_id=fork_id,
                outcome=outcome,
            )
            assert rec.outcome == outcome

    def test_rejects_empty_source_session_id(
        self, replay_service: ReplayService
    ) -> None:
        fork_id = _seed_fork(replay_service.conn)
        with pytest.raises(ValueError):
            replay_service.create_replay(
                source_session_id="",
                source_harness_run_id=None,
                fork_id=fork_id,
            )

    def test_rejects_empty_fork_id(
        self, replay_service: ReplayService
    ) -> None:
        with pytest.raises(ValueError):
            replay_service.create_replay(
                source_session_id="sess-X",
                source_harness_run_id=None,
                fork_id="",
            )

    def test_normalizes_non_dict_checkpoint(
        self, replay_service: ReplayService
    ) -> None:
        """Normalization errors bubble up before any insert."""
        fork_id = _seed_fork(replay_service.conn)
        with pytest.raises(ValueError):
            replay_service.create_replay(
                source_session_id="sess-Y",
                source_harness_run_id=None,
                fork_id=fork_id,
                checkpoint="not-a-dict",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# evaluate_equivalence — outcome-based policy
# ---------------------------------------------------------------------------


class TestEvaluateEquivalence:
    def test_matching_hashes_with_pass_verdict_is_equivalent(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        """Same artifact hashes + passing reviewer → equivalent."""
        workspace_id = _seed_workspace(replay_service.conn)
        source_ids = [
            _make_artifact(artifact_repo, workspace_id, content_hash=h)
            for h in ("hash-a", "hash-b")
        ]
        candidate_ids = [
            _make_artifact(artifact_repo, workspace_id, content_hash=h)
            for h in ("hash-a", "hash-b")
        ]
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            candidate_harness_run_id="run-candidate",
            reviewer_verdict="pass",
            source_artifact_ids=source_ids,
            candidate_artifact_ids=candidate_ids,
        )
        assert result["equivalent"] is True
        assert result["rule"] == EQUIVALENCE_RULE
        assert result["source_harness_run_id"] == "run-source"
        assert result["candidate_harness_run_id"] == "run-candidate"
        assert sorted(result["source_artifact_hashes"]) == [
            "hash-a",
            "hash-b",
        ]
        assert sorted(result["candidate_artifact_hashes"]) == [
            "hash-a",
            "hash-b",
        ]
        # Reasons explain the match.
        joined = " ".join(result["reasons"])
        assert "match" in joined.lower()

    def test_ignores_call_sequence_uses_only_final_state(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        """Different artifact sets → not equivalent, even with a
        'pass' verdict. The policy is hash-based, not sequence-based."""
        workspace_id = _seed_workspace(replay_service.conn)
        source_ids = [
            _make_artifact(artifact_repo, workspace_id, content_hash="hash-a"),
            _make_artifact(artifact_repo, workspace_id, content_hash="hash-b"),
        ]
        candidate_ids = [
            _make_artifact(artifact_repo, workspace_id, content_hash="hash-c"),
        ]
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            reviewer_verdict="pass",
            source_artifact_ids=source_ids,
            candidate_artifact_ids=candidate_ids,
        )
        assert result["equivalent"] is False
        joined = " ".join(result["reasons"])
        assert "differ" in joined.lower() or "differ" in joined

    def test_fail_verdict_overrides_matching_hashes(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        """Reviewer ``fail`` forces non-equivalence even when hashes match."""
        workspace_id = _seed_workspace(replay_service.conn)
        ids = [
            _make_artifact(artifact_repo, workspace_id, content_hash="hash-a")
        ]
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            candidate_harness_run_id="run-candidate",
            reviewer_verdict="fail",
            source_artifact_ids=ids,
            candidate_artifact_ids=ids,
        )
        assert result["equivalent"] is False
        joined = " ".join(result["reasons"])
        assert "fail" in joined.lower() or "overrid" in joined.lower()

    def test_blocked_verdict_overrides_matching_hashes(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        """Reviewer ``blocked`` also forces non-equivalence."""
        workspace_id = _seed_workspace(replay_service.conn)
        ids = [
            _make_artifact(artifact_repo, workspace_id, content_hash="hash-a")
        ]
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            reviewer_verdict="blocked",
            source_artifact_ids=ids,
            candidate_artifact_ids=ids,
        )
        assert result["equivalent"] is False
        joined = " ".join(result["reasons"])
        assert "block" in joined.lower() or "overrid" in joined.lower()

    def test_conditional_verdict_does_not_override_hashes(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        """``conditional`` is not in the failing-verdict set."""
        workspace_id = _seed_workspace(replay_service.conn)
        ids = [
            _make_artifact(artifact_repo, workspace_id, content_hash="hash-a")
        ]
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            reviewer_verdict="conditional",
            source_artifact_ids=ids,
            candidate_artifact_ids=ids,
        )
        assert result["equivalent"] is True

    def test_missing_source_evidence_is_not_equivalent(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        """No source artifact evidence → explicit non-equivalent result."""
        # Provide a real candidate artifact so we hit the source-missing
        # branch (otherwise the policy reports "neither side has evidence").
        workspace_id = _seed_workspace(replay_service.conn)
        cid = _make_artifact(
            artifact_repo, workspace_id, content_hash="hash-c"
        )
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            reviewer_verdict="pass",
            source_artifact_ids=None,
            candidate_artifact_ids=[cid],
        )
        assert result["equivalent"] is False
        assert any("source" in r.lower() for r in result["reasons"])
        assert any(
            "missing" in r.lower() or "evidence" in r.lower()
            for r in result["reasons"]
        )

    def test_missing_candidate_evidence_is_not_equivalent(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        workspace_id = _seed_workspace(replay_service.conn)
        sid = _make_artifact(
            artifact_repo, workspace_id, content_hash="hash-a"
        )
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            reviewer_verdict="pass",
            source_artifact_ids=[sid],
            candidate_artifact_ids=None,
        )
        assert result["equivalent"] is False
        assert any("candidate" in r.lower() for r in result["reasons"])

    def test_both_sides_empty_is_not_equivalent(
        self, replay_service: ReplayService
    ) -> None:
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            source_artifact_ids=None,
            candidate_artifact_ids=None,
        )
        assert result["equivalent"] is False
        assert result["source_artifact_hashes"] == []
        assert result["candidate_artifact_hashes"] == []
        # The reason must explicitly call out missing evidence on
        # BOTH sides.
        joined = " ".join(result["reasons"]).lower()
        assert "neither" in joined or "both" in joined or "either" in joined
        assert "missing" in joined or "evidence" in joined

    def test_unresolvable_artifact_ids_are_skipped(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        """Ids that don't resolve contribute nothing to the comparison."""
        workspace_id = _seed_workspace(replay_service.conn)
        real = _make_artifact(
            artifact_repo, workspace_id, content_hash="hash-real"
        )
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            source_artifact_ids=[real, "does-not-exist", ""],
            candidate_artifact_ids=[real, "also-missing"],
        )
        # Only ``hash-real`` survives; both sides match → equivalent.
        assert result["equivalent"] is True
        assert result["source_artifact_hashes"] == ["hash-real"]
        assert result["candidate_artifact_hashes"] == ["hash-real"]

    def test_artifacts_without_content_hash_are_skipped(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        """``content_hash IS NULL`` is not evidence of equivalence."""
        workspace_id = _seed_workspace(replay_service.conn)
        with_hash = _make_artifact(
            artifact_repo, workspace_id, content_hash="hash-1"
        )
        # Create an artifact with no content_hash.
        no_hash = artifact_repo.create(
            workspace_id=workspace_id,
            producer_session_id="producer-sess",
            artifact_kind="code",
            title="no-hash",
        )
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            source_artifact_ids=[with_hash, no_hash.artifact_id],
            candidate_artifact_ids=[with_hash],
        )
        # Source had one real hash; candidate had the same one. Match.
        assert result["equivalent"] is True
        # The no-hash artifact was filtered out of the resolved list.
        assert result["source_artifact_hashes"] == ["hash-1"]

    def test_rejects_empty_source_harness_run_id(
        self, replay_service: ReplayService
    ) -> None:
        with pytest.raises(ValueError):
            replay_service.evaluate_equivalence(
                source_harness_run_id="",
            )

    def test_result_is_set_based_ignoring_order(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        """Order does not matter for hash-set comparison."""
        workspace_id = _seed_workspace(replay_service.conn)
        source_ids = [
            _make_artifact(artifact_repo, workspace_id, content_hash=h)
            for h in ("hash-a", "hash-b", "hash-c")
        ]
        # Candidate has the same hashes in a different order.
        candidate_ids = list(reversed(source_ids))
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            source_artifact_ids=source_ids,
            candidate_artifact_ids=candidate_ids,
        )
        assert result["equivalent"] is True
        assert sorted(result["source_artifact_hashes"]) == sorted(
            result["candidate_artifact_hashes"]
        )


# ---------------------------------------------------------------------------
# list_replays_for_run
# ---------------------------------------------------------------------------


class TestListReplaysForRun:
    def test_filters_by_source_harness_run_id(
        self,
        replay_service: ReplayService,
        harness_run_repo: HarnessRunRepository,
    ) -> None:
        """Only replays whose ``source_harness_run_id`` matches are returned."""
        workspace_id = _seed_workspace(replay_service.conn)
        run_1 = _seed_harness_run(
            replay_service.conn, workspace_id, session_id="run-1-sess"
        )
        run_2 = _seed_harness_run(
            replay_service.conn, workspace_id, session_id="run-2-sess"
        )

        f1 = _seed_fork(replay_service.conn)
        f2 = _seed_fork(replay_service.conn)
        f3 = _seed_fork(replay_service.conn)
        f4 = _seed_fork(replay_service.conn)

        replay_service.create_replay(
            source_session_id="sess-A",
            source_harness_run_id=run_1,
            fork_id=f1,
        )
        replay_service.create_replay(
            source_session_id="sess-A",
            source_harness_run_id=run_1,
            fork_id=f2,
        )
        # Different run — should not appear.
        replay_service.create_replay(
            source_session_id="sess-A",
            source_harness_run_id=run_2,
            fork_id=f3,
        )
        # Session-scoped (no run) — should not appear here.
        replay_service.create_replay(
            source_session_id="sess-B",
            source_harness_run_id=None,
            fork_id=f4,
        )

        results = replay_service.list_replays_for_run(run_1)
        assert len(results) == 2
        assert all(r.source_harness_run_id == run_1 for r in results)

    def test_returns_empty_for_no_match(
        self, replay_service: ReplayService
    ) -> None:
        assert replay_service.list_replays_for_run("run-missing") == []

    def test_empty_harness_run_id_returns_empty(
        self, replay_service: ReplayService
    ) -> None:
        assert replay_service.list_replays_for_run("") == []


# ---------------------------------------------------------------------------
# get_replay_timeline
# ---------------------------------------------------------------------------


class TestGetReplayTimeline:
    def test_returns_session_scoped_replays(
        self,
        replay_service: ReplayService,
        harness_run_repo: HarnessRunRepository,
    ) -> None:
        workspace_id = _seed_workspace(replay_service.conn)
        run_1 = _seed_harness_run(
            replay_service.conn, workspace_id, session_id="run-1-sess"
        )

        f1 = _seed_fork(replay_service.conn)
        f2 = _seed_fork(replay_service.conn)
        f3 = _seed_fork(replay_service.conn)

        replay_service.create_replay(
            source_session_id="sess-X",
            source_harness_run_id=run_1,
            fork_id=f1,
        )
        replay_service.create_replay(
            source_session_id="sess-X",
            source_harness_run_id=None,
            fork_id=f2,
        )
        # Different session — should not appear.
        replay_service.create_replay(
            source_session_id="sess-Y",
            source_harness_run_id=None,
            fork_id=f3,
        )

        timeline = replay_service.get_replay_timeline("sess-X")
        assert len(timeline) == 2
        assert all(r.source_session_id == "sess-X" for r in timeline)
        # Ordered by created_at ASC.
        assert timeline == sorted(timeline, key=lambda r: r.created_at)

    def test_empty_session_returns_empty(
        self, replay_service: ReplayService
    ) -> None:
        assert replay_service.get_replay_timeline("nothing-here") == []

    def test_empty_source_session_id_returns_empty(
        self, replay_service: ReplayService
    ) -> None:
        assert replay_service.get_replay_timeline("") == []


# ---------------------------------------------------------------------------
# Integration — equivalence based on final state, not call sequence
# ---------------------------------------------------------------------------


class TestEquivalenceUsesFinalStateNotCallSequence:
    """Phase 7 §11 / 03_DOMAIN_MODEL.md §6: equivalence is outcome-based."""

    def test_same_artifacts_same_verdict_is_equivalent(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        workspace_id = _seed_workspace(replay_service.conn)
        # Source and candidate use entirely different artifact ids
        # (as if produced by different tool-call sequences) but end
        # with the same content hashes.
        s1 = _make_artifact(artifact_repo, workspace_id, content_hash="H1")
        s2 = _make_artifact(artifact_repo, workspace_id, content_hash="H2")
        c1 = _make_artifact(artifact_repo, workspace_id, content_hash="H1")
        c2 = _make_artifact(artifact_repo, workspace_id, content_hash="H2")
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            candidate_harness_run_id="run-candidate",
            reviewer_verdict="pass",
            source_artifact_ids=[s1, s2],
            candidate_artifact_ids=[c1, c2],
        )
        # Different artifact ids (different tool-call sequences) but
        # same final-state hashes → equivalent.
        assert result["equivalent"] is True

    def test_same_verdict_different_final_state_is_not_equivalent(
        self,
        replay_service: ReplayService,
        artifact_repo: ArtifactRepository,
    ) -> None:
        workspace_id = _seed_workspace(replay_service.conn)
        s1 = _make_artifact(artifact_repo, workspace_id, content_hash="H1")
        c1 = _make_artifact(artifact_repo, workspace_id, content_hash="H2")
        result = replay_service.evaluate_equivalence(
            source_harness_run_id="run-source",
            reviewer_verdict="pass",
            source_artifact_ids=[s1],
            candidate_artifact_ids=[c1],
        )
        assert result["equivalent"] is False
