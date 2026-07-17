"""Tests for ReviewService — review workflow hardening (Phase 7)."""

from __future__ import annotations

import sqlite3
import time

import pytest

from agent_workbench.models.artifact import ArtifactRepository
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.review_record import (
    ReviewRecord,
    ReviewRecordRepository,
)
from agent_workbench.models.workspace import Workspace, WorkspaceRepository
from agent_workbench.services.review_service import (
    ReviewService,
    ReviewServiceError,
    VERDICT_BLOCKED,
    VERDICT_CONDITIONAL,
    VERDICT_FAIL,
    VERDICT_PASS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_id(db: sqlite3.Connection) -> str:
    repo = WorkspaceRepository(db)
    ws: Workspace = repo.create(tenant_id="tenant-1", name="Test Workspace")
    return ws.workspace_id


@pytest.fixture
def review_service(db: sqlite3.Connection) -> ReviewService:
    return ReviewService(db)


@pytest.fixture
def review_repo(db: sqlite3.Connection) -> ReviewRecordRepository:
    return ReviewRecordRepository(db)


@pytest.fixture
def harness_run_repo(db: sqlite3.Connection) -> HarnessRunRepository:
    return HarnessRunRepository(db)


@pytest.fixture
def artifact_repo(db: sqlite3.Connection) -> ArtifactRepository:
    return ArtifactRepository(db)


# ---------------------------------------------------------------------------
# create_review — append-only and validation
# ---------------------------------------------------------------------------


def test_create_review_returns_persisted_record(
    review_service: ReviewService,
    workspace_id: str,
) -> None:
    rec = review_service.create_review(
        workspace_id=workspace_id,
        target_kind="task_spec",
        target_id="ts-1",
        verdict=VERDICT_PASS,
        findings_ref="s3://findings/r1.json",
        criteria_eval={"accuracy": 0.95},
    )
    assert isinstance(rec, ReviewRecord)
    assert rec.review_id
    assert rec.workspace_id == workspace_id
    assert rec.target_kind == "task_spec"
    assert rec.target_id == "ts-1"
    assert rec.verdict == VERDICT_PASS
    assert rec.findings_ref == "s3://findings/r1.json"
    assert rec.criteria_eval == {"accuracy": 0.95}
    assert rec.created_at > 0


def test_reviews_are_append_only_with_preserved_order(
    review_service: ReviewService,
    review_repo: ReviewRecordRepository,
    workspace_id: str,
) -> None:
    """Each call must produce a new row with a later created_at, and
    earlier rows must remain unchanged (append-only)."""
    target_kind = "task_spec"
    target_id = "ts-append"

    first = review_service.create_review(
        workspace_id=workspace_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=VERDICT_PASS,
    )
    # Force a measurable time gap so the created_at ordering is
    # deterministic across rows on any platform.
    time.sleep(0.005)
    second = review_service.create_review(
        workspace_id=workspace_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=VERDICT_CONDITIONAL,
    )
    time.sleep(0.005)
    third = review_service.create_review(
        workspace_id=workspace_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=VERDICT_FAIL,
    )

    # Distinct ids
    assert len({first.review_id, second.review_id, third.review_id}) == 3
    # Strictly non-decreasing created_at
    assert first.created_at <= second.created_at <= third.created_at
    # Repository ordering matches insertion order
    listed = review_repo.list_by_target(target_kind, target_id)
    assert [r.review_id for r in listed] == [
        first.review_id,
        second.review_id,
        third.review_id,
    ]


def test_list_reviews_returns_empty_list_for_unknown_target(
    review_service: ReviewService,
) -> None:
    assert review_service.list_reviews("task_spec", "never-reviewed") == []


def test_latest_review_returns_none_when_no_reviews(
    review_service: ReviewService,
) -> None:
    assert review_service.latest_review("session", "sess-x") is None


def test_latest_review_returns_most_recent(
    review_service: ReviewService,
    workspace_id: str,
) -> None:
    a = review_service.create_review(
        workspace_id=workspace_id,
        target_kind="artifact",
        target_id="art-1",
        verdict=VERDICT_PASS,
    )
    time.sleep(0.005)
    b = review_service.create_review(
        workspace_id=workspace_id,
        target_kind="artifact",
        target_id="art-1",
        verdict=VERDICT_FAIL,
    )
    latest = review_service.latest_review("artifact", "art-1")
    assert latest is not None
    assert latest.review_id == b.review_id
    assert latest.verdict == VERDICT_FAIL
    # And the earlier record is still there, untouched.
    assert latest.created_at >= a.created_at


# ---------------------------------------------------------------------------
# create_review — validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_kind": "nope", "target_id": "x", "verdict": VERDICT_PASS},
        {"target_kind": "task_spec", "target_id": "", "verdict": VERDICT_PASS},
        {"target_kind": "task_spec", "target_id": "x", "verdict": "maybe"},
    ],
)
def test_create_review_validates_inputs(
    review_service: ReviewService,
    workspace_id: str,
    kwargs: dict,
) -> None:
    with pytest.raises(ReviewServiceError):
        review_service.create_review(workspace_id=workspace_id, **kwargs)


def test_create_review_rejects_empty_workspace_id(
    review_service: ReviewService,
) -> None:
    with pytest.raises(ReviewServiceError):
        review_service.create_review(
            workspace_id="",
            target_kind="task_spec",
            target_id="x",
            verdict=VERDICT_PASS,
        )


# ---------------------------------------------------------------------------
# summarize_review_state — blocking semantics
# ---------------------------------------------------------------------------


def test_summarize_with_no_reviews_is_not_blocking(
    review_service: ReviewService,
) -> None:
    summary = review_service.summarize_review_state("task_spec", "ts-empty")
    assert summary["target_kind"] == "task_spec"
    assert summary["target_id"] == "ts-empty"
    assert summary["review_count"] == 0
    assert summary["latest_verdict"] is None
    assert summary["latest_review_id"] is None
    assert summary["latest_reviewed_at"] is None
    assert summary["blocking"] is False


def test_summarize_blocking_when_latest_is_fail(
    review_service: ReviewService,
    workspace_id: str,
) -> None:
    target_kind = "task_spec"
    target_id = "ts-fail"
    review_service.create_review(
        workspace_id=workspace_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=VERDICT_PASS,
    )
    time.sleep(0.005)
    review_service.create_review(
        workspace_id=workspace_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=VERDICT_FAIL,
    )
    summary = review_service.summarize_review_state(target_kind, target_id)
    assert summary["review_count"] == 2
    assert summary["latest_verdict"] == VERDICT_FAIL
    assert summary["blocking"] is True


def test_summarize_blocking_when_latest_is_blocked(
    review_service: ReviewService,
    workspace_id: str,
) -> None:
    target_kind = "harness_run"
    target_id = "hr-1"
    review_service.create_review(
        workspace_id=workspace_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=VERDICT_BLOCKED,
    )
    summary = review_service.summarize_review_state(target_kind, target_id)
    assert summary["latest_verdict"] == VERDICT_BLOCKED
    assert summary["blocking"] is True


@pytest.mark.parametrize("verdict", [VERDICT_PASS, VERDICT_CONDITIONAL])
def test_summarize_not_blocking_for_pass_or_conditional(
    review_service: ReviewService,
    workspace_id: str,
    verdict: str,
) -> None:
    target_kind = "task_spec"
    target_id = f"ts-{verdict}"
    review_service.create_review(
        workspace_id=workspace_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=verdict,
    )
    summary = review_service.summarize_review_state(target_kind, target_id)
    assert summary["latest_verdict"] == verdict
    assert summary["blocking"] is False


def test_summarize_uses_latest_verdict_not_cumulative(
    review_service: ReviewService,
    workspace_id: str,
) -> None:
    """A blocking verdict followed by a pass must unblock the target."""
    target_kind = "session"
    target_id = "sess-1"
    review_service.create_review(
        workspace_id=workspace_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=VERDICT_FAIL,
    )
    time.sleep(0.005)
    review_service.create_review(
        workspace_id=workspace_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=VERDICT_PASS,
    )
    summary = review_service.summarize_review_state(target_kind, target_id)
    assert summary["review_count"] == 2
    assert summary["latest_verdict"] == VERDICT_PASS
    assert summary["blocking"] is False


# ---------------------------------------------------------------------------
# build_review_bundle
# ---------------------------------------------------------------------------


def test_build_review_bundle_for_harness_run_includes_run_and_artifacts(
    review_service: ReviewService,
    harness_run_repo: HarnessRunRepository,
    artifact_repo: ArtifactRepository,
    workspace_id: str,
) -> None:
    """When the target is a harness_run, the bundle should include
    that run plus every artifact it produced."""
    session_id = "sess-run-1"
    harness_run = harness_run_repo.create(
        workspace_id=workspace_id,
        session_id=session_id,
        harness_type="hermes",
        status="completed",
    )
    art_a = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id=session_id,
        producer_harness_run_id=harness_run.harness_run_id,
        artifact_kind="code",
        title="main.py",
    )
    art_b = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id=session_id,
        producer_harness_run_id=harness_run.harness_run_id,
        artifact_kind="doc",
        title="README.md",
    )
    # Unrelated artifact for a different run — must not appear.
    other_run = harness_run_repo.create(
        workspace_id=workspace_id,
        session_id=session_id,
        harness_type="shell",
    )
    artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id=session_id,
        producer_harness_run_id=other_run.harness_run_id,
        artifact_kind="code",
    )

    # Two reviews on the harness run.
    review_service.create_review(
        workspace_id=workspace_id,
        target_kind="harness_run",
        target_id=harness_run.harness_run_id,
        verdict=VERDICT_PASS,
    )
    time.sleep(0.005)
    review_service.create_review(
        workspace_id=workspace_id,
        target_kind="harness_run",
        target_id=harness_run.harness_run_id,
        verdict=VERDICT_FAIL,
    )

    bundle = review_service.build_review_bundle(
        "harness_run", harness_run.harness_run_id
    )

    assert bundle["target_kind"] == "harness_run"
    assert bundle["target_id"] == harness_run.harness_run_id
    assert len(bundle["reviews"]) == 2
    assert bundle["reviews"][0]["verdict"] == VERDICT_PASS
    assert bundle["reviews"][1]["verdict"] == VERDICT_FAIL
    assert bundle["summary"]["latest_verdict"] == VERDICT_FAIL
    assert bundle["summary"]["blocking"] is True

    related = bundle["related"]
    assert related["harness_run"] is not None
    assert related["harness_run"]["harness_run_id"] == harness_run.harness_run_id
    artifact_ids = {a["artifact_id"] for a in related["artifacts"]}
    assert artifact_ids == {art_a.artifact_id, art_b.artifact_id}


def test_build_review_bundle_for_harness_run_with_no_artifacts(
    review_service: ReviewService,
    harness_run_repo: HarnessRunRepository,
    workspace_id: str,
) -> None:
    harness_run = harness_run_repo.create(
        workspace_id=workspace_id,
        session_id="sess-empty",
        harness_type="opencode",
    )
    bundle = review_service.build_review_bundle(
        "harness_run", harness_run.harness_run_id
    )
    assert bundle["reviews"] == []
    assert bundle["summary"]["review_count"] == 0
    assert bundle["related"]["harness_run"] is not None
    assert bundle["related"]["harness_run"]["harness_run_id"] == harness_run.harness_run_id
    assert bundle["related"]["artifacts"] == []


def test_build_review_bundle_for_session_includes_runs_and_artifacts(
    review_service: ReviewService,
    harness_run_repo: HarnessRunRepository,
    artifact_repo: ArtifactRepository,
    workspace_id: str,
) -> None:
    """For ``target_kind == 'session'`` the bundle should include all
    harness runs and all artifacts belonging to the session."""
    session_id = "sess-bundle-1"
    other_session = "sess-other"

    run_a = harness_run_repo.create(
        workspace_id=workspace_id,
        session_id=session_id,
        harness_type="hermes",
    )
    run_b = harness_run_repo.create(
        workspace_id=workspace_id,
        session_id=session_id,
        harness_type="shell",
    )
    harness_run_repo.create(
        workspace_id=workspace_id,
        session_id=other_session,
        harness_type="hermes",
    )

    art_a = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id=session_id,
        producer_harness_run_id=run_a.harness_run_id,
        artifact_kind="code",
    )
    art_b = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id=session_id,
        artifact_kind="doc",
    )
    # Artifact in the other session — must not appear.
    artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id=other_session,
        artifact_kind="code",
    )

    review_service.create_review(
        workspace_id=workspace_id,
        target_kind="session",
        target_id=session_id,
        verdict=VERDICT_CONDITIONAL,
    )

    bundle = review_service.build_review_bundle("session", session_id)

    assert bundle["summary"]["latest_verdict"] == VERDICT_CONDITIONAL
    assert bundle["summary"]["blocking"] is False
    assert len(bundle["reviews"]) == 1

    related = bundle["related"]
    run_ids = {r["harness_run_id"] for r in related["harness_runs"]}
    assert run_ids == {run_a.harness_run_id, run_b.harness_run_id}
    art_ids = {a["artifact_id"] for a in related["artifacts"]}
    assert art_ids == {art_a.artifact_id, art_b.artifact_id}


def test_build_review_bundle_for_artifact_includes_artifact_and_run(
    review_service: ReviewService,
    harness_run_repo: HarnessRunRepository,
    artifact_repo: ArtifactRepository,
    workspace_id: str,
) -> None:
    session_id = "sess-art-bundle"
    run = harness_run_repo.create(
        workspace_id=workspace_id,
        session_id=session_id,
        harness_type="hermes",
    )
    art = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id=session_id,
        producer_harness_run_id=run.harness_run_id,
        artifact_kind="code",
    )
    review_service.create_review(
        workspace_id=workspace_id,
        target_kind="artifact",
        target_id=art.artifact_id,
        verdict=VERDICT_PASS,
    )
    bundle = review_service.build_review_bundle("artifact", art.artifact_id)
    assert bundle["related"]["artifact"] is not None
    assert bundle["related"]["artifact"]["artifact_id"] == art.artifact_id
    assert bundle["related"]["harness_run"] is not None
    assert bundle["related"]["harness_run"]["harness_run_id"] == run.harness_run_id


def test_build_review_bundle_handles_missing_target(
    review_service: ReviewService,
) -> None:
    """Bundle for a never-reviewed target must still be well-formed."""
    bundle = review_service.build_review_bundle("task_spec", "missing-id")
    assert bundle["reviews"] == []
    assert bundle["summary"]["review_count"] == 0
    assert bundle["summary"]["blocking"] is False
    # task_spec has no related artifact/harness_run mapping.
    assert bundle["related"]["artifacts"] == []
    assert bundle["related"]["harness_runs"] == []
    assert bundle["related"]["artifact"] is None
    assert bundle["related"]["harness_run"] is None
