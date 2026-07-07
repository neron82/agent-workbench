"""Tests for VerificationService.

These tests exercise the cross-harness verification surface end to
end against a real (in-memory) ``workbench.db`` created by the
shared ``db`` fixture in :mod:`tests.conftest`. They cover the six
acceptance criteria spelled out in the Phase 7 contract:

1. exact replay equivalence note text (UI spec §11)
2. verification readiness blocked by active/running status
3. verification readiness blocked by missing reviews
4. verification readiness blocked by missing artifact hashes
5. completed/reviewed run with hashed artifacts becomes
   ``verification_ready=True``
6. session surface aggregates multiple runs correctly
"""

from __future__ import annotations

import sqlite3
import time
import uuid

import pytest

from agent_workbench.models.artifact import ArtifactRepository
from agent_workbench.models.fork_record import ForkRecordRepository
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.replay_record import ReplayRecordRepository
from agent_workbench.models.review_record import ReviewRecordRepository
from agent_workbench.models.task_spec import TaskSpecRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.verification_service import (
    REPLAY_EQUIVALENCE_NOTE,
    VERIFIABLE_RUN_STATUSES,
    VerificationService,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def workspace_id(db: sqlite3.Connection) -> str:
    repo = WorkspaceRepository(db)
    ws = repo.create(tenant_id="tenant-1", name="Verification WS")
    return ws.workspace_id


@pytest.fixture
def task_spec_id(workspace_id: str, db: sqlite3.Connection) -> str:
    repo = TaskSpecRepository(db)
    ts = repo.create(workspace_id=workspace_id, objective="verify things")
    return ts.task_spec_id


@pytest.fixture
def service(db: sqlite3.Connection) -> VerificationService:
    return VerificationService(db)


def _make_run(
    db: sqlite3.Connection,
    workspace_id: str,
    session_id: str,
    *,
    status: str = "reviewable",
    harness_type: str = "hermes",
    task_spec_id=None,
    started_at: float = 0.0,
    ended_at: float = 0.0,
) -> str:
    repo = HarnessRunRepository(db)
    run = repo.create(
        workspace_id=workspace_id,
        session_id=session_id,
        harness_type=harness_type,
        task_spec_id=task_spec_id,
        status=status,
    )
    if started_at or ended_at:
        repo.update_status(
            run.harness_run_id,
            status=status,
            started_at=started_at or None,
            ended_at=ended_at or None,
        )
    return run.harness_run_id


def _add_artifact(
    db: sqlite3.Connection,
    *,
    workspace_id: str,
    session_id: str,
    harness_run_id: str,
    title: str,
    content_hash=None,
    task_spec_id=None,
) -> str:
    repo = ArtifactRepository(db)
    art = repo.create(
        workspace_id=workspace_id,
        producer_session_id=session_id,
        producer_harness_run_id=harness_run_id,
        task_spec_id=task_spec_id,
        artifact_kind="code",
        title=title,
        content_hash=content_hash,
    )
    return art.artifact_id


def _add_review(
    db: sqlite3.Connection,
    *,
    workspace_id: str,
    target_kind: str,
    target_id: str,
    verdict: str = "pass",
) -> str:
    repo = ReviewRecordRepository(db)
    rec = repo.create(
        workspace_id=workspace_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=verdict,
    )
    return rec.review_id


def _add_replay(
    db: sqlite3.Connection,
    *,
    source_session_id: str,
    source_harness_run_id: str,
    workspace_id: str,
    outcome: str = "completed",
) -> str:
    """Insert a replay record. Requires a real fork row for the FK."""
    fork_repo = ForkRecordRepository(db)
    fork = fork_repo.create(
        parent_session_id=source_session_id,
        child_session_id=source_session_id + "-replay",
        fork_kind="replay",
        fork_reason="verification test",
        initiated_by="system",
        summary_ref="replay fork for verification test",
    )
    # The ForkRecord.create method does not necessarily exist on this
    # repo — fall back to a direct insert for the test harness.
    if fork is None:
        fork_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO fork_records "
            "(fork_id, parent_session_id, child_session_id, fork_kind, "
            " fork_reason, initiated_by, summary_ref, "
            " bootstrap_context_role_internal) "
            "VALUES (?, ?, ?, 'replay', 'verification test', 'system', "
            " 'replay fork', 'fork_context')",
            (
                fork_id,
                source_session_id,
                source_session_id + "-replay",
            ),
        )
        db.commit()
    else:
        fork_id = fork.fork_id

    repo = ReplayRecordRepository(db)
    rec = repo.create(
        source_session_id=source_session_id,
        source_harness_run_id=source_harness_run_id,
        fork_id=fork_id,
        replay_scope="full",
        outcome=outcome,
    )
    return rec.replay_id


# ----------------------------------------------------------------------
# Surface shape
# ----------------------------------------------------------------------


class TestRunSurfaceShape:
    def test_required_keys_present(self, service, workspace_id, db):
        run_id = _make_run(db, workspace_id, "sess-shape", status="completed")
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-shape",
            harness_run_id=run_id,
            title="a.py",
            content_hash="sha256-aaa",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )

        surface = service.get_run_verification_surface(run_id)

        for key in (
            "harness_run_id",
            "session_id",
            "harness_type",
            "status",
            "artifacts",
            "reviews",
            "replays",
            "latest_review_verdict",
            "replay_equivalence_note",
            "verification_ready",
            "blockers",
        ):
            assert key in surface, f"Missing required key: {key!r}"

        assert surface["harness_run_id"] == run_id
        assert surface["session_id"] == "sess-shape"
        assert surface["harness_type"] == "hermes"
        assert surface["status"] == "completed"
        assert isinstance(surface["artifacts"], list)
        assert isinstance(surface["reviews"], list)
        assert isinstance(surface["replays"], list)
        assert isinstance(surface["blockers"], list)
        assert isinstance(surface["verification_ready"], bool)

    def test_unknown_run_raises_lookup_error(self, service):
        with pytest.raises(LookupError):
            service.get_run_verification_surface("does-not-exist")

    def test_session_without_runs_reports_blocker(self, service):
        surface = service.get_session_verification_surface("empty-session")
        assert surface["run_count"] == 0
        assert surface["verification_ready"] is False
        assert any("No harness runs" in b for b in surface["blockers"])


# ----------------------------------------------------------------------
# 1. Exact replay equivalence note text
# ----------------------------------------------------------------------


class TestReplayEquivalenceNote:
    def test_note_exact_text_on_run_surface(
        self, service, workspace_id, db
    ):
        run_id = _make_run(db, workspace_id, "sess-note", status="completed")
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-note",
            harness_run_id=run_id,
            title="x.py",
            content_hash="sha256-xyz",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )
        surface = service.get_run_verification_surface(run_id)
        assert surface["replay_equivalence_note"] == REPLAY_EQUIVALENCE_NOTE
        assert surface["replay_equivalence_note"] == (
            "Replay equivalence means equivalent final state and "
            "reviewer-judged outcome, not identical tool-call sequence."
        )

    def test_note_exact_text_on_session_surface(
        self, service, workspace_id, db
    ):
        run_id = _make_run(db, workspace_id, "sess-note-2", status="completed")
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-note-2",
            harness_run_id=run_id,
            title="y.py",
            content_hash="sha256-yyy",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )
        surface = service.get_session_verification_surface("sess-note-2")
        assert surface["replay_equivalence_note"] == REPLAY_EQUIVALENCE_NOTE


# ----------------------------------------------------------------------
# 2. Blocked by active/running status
# ----------------------------------------------------------------------


class TestBlockedByActiveStatus:
    @pytest.mark.parametrize(
        "active_status",
        ["queued", "starting", "running", "blocked", "stopping"],
    )
    def test_active_status_blocks_verification(
        self, service, workspace_id, db, active_status
    ):
        run_id = _make_run(
            db, workspace_id, "sess-active", status=active_status
        )
        # Even with reviews and hashed artifacts, an active run must
        # not be considered verification-ready.
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-active",
            harness_run_id=run_id,
            title="active.py",
            content_hash="sha256-active",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["status"] == active_status
        assert surface["verification_ready"] is False
        assert any("Run status" in b for b in surface["blockers"])

    def test_verifiable_status_set_contents(self):
        # The status whitelist is a public contract — pin it so a
        # future schema change can't silently weaken it.
        assert VERIFIABLE_RUN_STATUSES == frozenset(
            {"reviewable", "completed", "failed", "cancelled"}
        )


# ----------------------------------------------------------------------
# 3. Blocked by missing reviews
# ----------------------------------------------------------------------


class TestBlockedByMissingReviews:
    def test_no_reviews_blocks_verification(
        self, service, workspace_id, db
    ):
        run_id = _make_run(
            db, workspace_id, "sess-noreviews", status="completed"
        )
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-noreviews",
            harness_run_id=run_id,
            title="noreviews.py",
            content_hash="sha256-nr",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["reviews"] == []
        assert surface["latest_review_verdict"] is None
        assert surface["verification_ready"] is False
        assert any("No review record" in b for b in surface["blockers"])

    def test_review_targets_artifact_also_unblocks(
        self, service, workspace_id, db
    ):
        # A review that targets an artifact of the run is sufficient.
        run_id = _make_run(
            db, workspace_id, "sess-artreview", status="completed"
        )
        art_id = _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-artreview",
            harness_run_id=run_id,
            title="artreview.py",
            content_hash="sha256-ar",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="artifact",
            target_id=art_id,
            verdict="pass",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["verification_ready"] is True
        assert surface["latest_review_verdict"] == "pass"

    def test_review_targets_task_spec_also_unblocks(
        self, service, workspace_id, task_spec_id, db
    ):
        run_id = _make_run(
            db,
            workspace_id,
            "sess-tsreview",
            status="completed",
            task_spec_id=task_spec_id,
        )
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-tsreview",
            harness_run_id=run_id,
            title="tsreview.py",
            content_hash="sha256-ts",
            task_spec_id=task_spec_id,
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="task_spec",
            target_id=task_spec_id,
            verdict="conditional",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["verification_ready"] is True
        assert surface["latest_review_verdict"] == "conditional"

    def test_latest_verdict_is_most_recent(
        self, service, workspace_id, db
    ):
        run_id = _make_run(
            db, workspace_id, "sess-latest", status="completed"
        )
        # Insert two reviews with deliberately-staggered timestamps
        # so the "latest" verdict is unambiguous.
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-latest",
            harness_run_id=run_id,
            title="latest.py",
            content_hash="sha256-latest",
        )
        first_id = _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="fail",
        )
        # Force the second review to be created *after* the first.
        time.sleep(0.01)
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["latest_review_verdict"] == "pass"
        # Both reviews should be present, deduped by id.
        assert len(surface["reviews"]) == 2
        assert first_id in {r["review_id"] for r in surface["reviews"]}


# ----------------------------------------------------------------------
# 4. Blocked by missing artifact hashes
# ----------------------------------------------------------------------


class TestBlockedByMissingArtifactHashes:
    def test_unhashed_artifact_blocks_verification(
        self, service, workspace_id, db
    ):
        run_id = _make_run(
            db, workspace_id, "sess-unhashed", status="completed"
        )
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-unhashed",
            harness_run_id=run_id,
            title="unhashed.py",
            content_hash=None,  # explicitly unhashed
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["verification_ready"] is False
        assert any("content_hash" in b for b in surface["blockers"])

    def test_empty_string_hash_is_treated_as_missing(
        self, service, workspace_id, db
    ):
        run_id = _make_run(
            db, workspace_id, "sess-emptyhash", status="completed"
        )
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-emptyhash",
            harness_run_id=run_id,
            title="emptyhash.py",
            content_hash="",  # empty string ≠ a real hash
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["verification_ready"] is False
        assert any("content_hash" in b for b in surface["blockers"])

    def test_run_with_no_artifacts_is_not_blocked_by_hash_rule(
        self, service, workspace_id, db
    ):
        # The contract says: "verification_ready=False if any
        # artifact linked to the run has missing content_hash". When
        # zero artifacts are linked, the predicate is vacuously
        # satisfied — the service must not block on a missing-hash
        # condition that does not exist. Other blockers (e.g. no
        # review) are a separate concern.
        run_id = _make_run(
            db, workspace_id, "sess-noart", status="completed"
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["artifacts"] == []
        # No content-hash blocker should be raised.
        assert not any("content_hash" in b for b in surface["blockers"])
        # The run has a review and is in a terminal status with no
        # hash blocker, so it is verification-ready.
        assert surface["verification_ready"] is True


# ----------------------------------------------------------------------
# 5. Happy path: completed + reviewed + hashed → ready
# ----------------------------------------------------------------------


class TestVerificationReady:
    def test_completed_reviewed_hashed_is_ready(
        self, service, workspace_id, db
    ):
        run_id = _make_run(
            db, workspace_id, "sess-happy", status="completed"
        )
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-happy",
            harness_run_id=run_id,
            title="happy.py",
            content_hash="sha256-happy",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["verification_ready"] is True
        assert surface["blockers"] == []

    def test_replay_record_surfaces_even_when_ready(
        self, service, workspace_id, db
    ):
        run_id = _make_run(
            db, workspace_id, "sess-replay", status="completed"
        )
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-replay",
            harness_run_id=run_id,
            title="r.py",
            content_hash="sha256-r",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )
        _add_replay(
            db,
            source_session_id="sess-replay",
            source_harness_run_id=run_id,
            workspace_id=workspace_id,
            outcome="completed",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["verification_ready"] is True
        assert len(surface["replays"]) == 1
        replay = surface["replays"][0]
        assert replay["outcome"] == "completed"
        assert replay["equivalence_rule"] == "final_state_plus_reviewer_judgment"

    @pytest.mark.parametrize(
        "terminal_status", ["reviewable", "completed", "failed", "cancelled"]
    )
    def test_all_terminal_statuses_can_be_ready(
        self, service, workspace_id, db, terminal_status
    ):
        run_id = _make_run(
            db,
            workspace_id,
            f"sess-{terminal_status}",
            status=terminal_status,
        )
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id=f"sess-{terminal_status}",
            harness_run_id=run_id,
            title=f"{terminal_status}.py",
            content_hash=f"sha256-{terminal_status}",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )

        surface = service.get_run_verification_surface(run_id)
        assert surface["status"] == terminal_status
        assert surface["verification_ready"] is True, surface["blockers"]


# ----------------------------------------------------------------------
# 6. Session surface aggregation
# ----------------------------------------------------------------------


class TestSessionSurfaceAggregation:
    def test_session_aggregates_multiple_runs(
        self, service, workspace_id, db
    ):
        session_id = "sess-multi"

        # Run A: ready.
        run_a = _make_run(db, workspace_id, session_id, status="completed")
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id=session_id,
            harness_run_id=run_a,
            title="a.py",
            content_hash="sha256-a",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_a,
            verdict="pass",
        )

        # Run B: still running, so the session is *not* ready.
        run_b = _make_run(db, workspace_id, session_id, status="running")
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id=session_id,
            harness_run_id=run_b,
            title="b.py",
            content_hash="sha256-b",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_b,
            verdict="pass",
        )

        # Run C: completed but no review, no artifacts — extra blocker.
        run_c = _make_run(
            db, workspace_id, session_id, status="cancelled"
        )

        # A session-level review should also show up in the totals.
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="session",
            target_id=session_id,
            verdict="conditional",
        )

        # Plus a session-wide replay for the ready run.
        _add_replay(
            db,
            source_session_id=session_id,
            source_harness_run_id=run_a,
            workspace_id=workspace_id,
            outcome="completed",
        )

        surface = service.get_session_verification_surface(session_id)

        assert surface["session_id"] == session_id
        assert surface["run_count"] == 3
        # Only run A is fully verified.
        assert surface["verification_ready_run_count"] == 1
        # Artifacts are deduped by the underlying list_by_session,
        # so we expect 2 artifacts.
        assert surface["artifact_count"] == 2
        # Reviews = 2 harness_run reviews + 1 session review = 3.
        assert surface["review_count"] == 3
        # Replays scoped to the session via source_session_id = 1.
        assert surface["replay_count"] == 1
        # Session-level latest verdict should be the most recent one.
        assert surface["latest_review_verdict"] in {"pass", "conditional"}
        # Session is not verification-ready because runs B and C are
        # not yet ready.
        assert surface["verification_ready"] is False
        # Per-run breakdown is present and ordered the same as the
        # underlying harness_runs list.
        assert [r["harness_run_id"] for r in surface["runs"]] == [
            run_a,
            run_b,
            run_c,
        ]
        ready_run_ids = [
            r["harness_run_id"]
            for r in surface["runs"]
            if r["verification_ready"]
        ]
        assert ready_run_ids == [run_a]
        # Blockers from individual runs are surfaced session-wide.
        assert any("Run status" in b for b in surface["blockers"])
        assert any("No review record" in b for b in surface["blockers"])

    def test_session_fully_ready_when_all_runs_ready(
        self, service, workspace_id, db
    ):
        session_id = "sess-allready"
        for idx in range(2):
            run_id = _make_run(
                db, workspace_id, session_id, status="completed"
            )
            _add_artifact(
                db,
                workspace_id=workspace_id,
                session_id=session_id,
                harness_run_id=run_id,
                title=f"f{idx}.py",
                content_hash=f"sha256-f{idx}",
            )
            _add_review(
                db,
                workspace_id=workspace_id,
                target_kind="harness_run",
                target_id=run_id,
                verdict="pass",
            )

        surface = service.get_session_verification_surface(session_id)
        assert surface["run_count"] == 2
        assert surface["verification_ready_run_count"] == 2
        assert surface["verification_ready"] is True
        assert surface["blockers"] == []


# ----------------------------------------------------------------------
# explain_blockers()
# ----------------------------------------------------------------------


class TestExplainBlockers:
    def test_returns_human_readable_blockers(self, service, workspace_id, db):
        run_id = _make_run(
            db, workspace_id, "sess-explain", status="running"
        )
        surface = service.get_run_verification_surface(run_id)
        explanation = VerificationService.explain_blockers(surface)
        assert isinstance(explanation, list)
        assert all(isinstance(b, str) for b in explanation)
        # The status blocker should be present and human-readable.
        assert any("Run status" in b for b in explanation)

    def test_empty_blockers_returns_empty_list(
        self, service, workspace_id, db
    ):
        run_id = _make_run(
            db, workspace_id, "sess-emptyblock", status="completed"
        )
        _add_artifact(
            db,
            workspace_id=workspace_id,
            session_id="sess-emptyblock",
            harness_run_id=run_id,
            title="e.py",
            content_hash="sha256-e",
        )
        _add_review(
            db,
            workspace_id=workspace_id,
            target_kind="harness_run",
            target_id=run_id,
            verdict="pass",
        )
        surface = service.get_run_verification_surface(run_id)
        assert surface["blockers"] == []
        assert VerificationService.explain_blockers(surface) == []

    def test_explain_blockers_is_deterministic(
        self, service, workspace_id, db
    ):
        run_id = _make_run(
            db, workspace_id, "sess-deter", status="queued"
        )
        surface = service.get_run_verification_surface(run_id)
        first = VerificationService.explain_blockers(surface)
        second = VerificationService.explain_blockers(surface)
        assert first == second

    def test_explain_blockers_handles_missing_blockers_key(self):
        # Robustness: explain_blockers should not crash on a surface
        # dict that lacks the optional ``blockers`` key.
        result = VerificationService.explain_blockers({})
        assert result == []
