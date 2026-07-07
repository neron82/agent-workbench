"""Tests for ArtifactVerifier — artifact integrity verification (Phase 7)."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from agent_workbench.models.artifact import Artifact, ArtifactRepository
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.workspace import Workspace, WorkspaceRepository
from agent_workbench.services.artifact_verifier import ArtifactVerifier


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_id(db: sqlite3.Connection) -> str:
    repo = WorkspaceRepository(db)
    ws: Workspace = repo.create(tenant_id="tenant-1", name="Test Workspace")
    return ws.workspace_id


@pytest.fixture
def artifact_repo(db: sqlite3.Connection) -> ArtifactRepository:
    return ArtifactRepository(db)


@pytest.fixture
def harness_run_repo(db: sqlite3.Connection) -> HarnessRunRepository:
    return HarnessRunRepository(db)


@pytest.fixture
def verifier(db: sqlite3.Connection) -> ArtifactVerifier:
    return ArtifactVerifier(db)


# ---------------------------------------------------------------------------
# compute_content_hash
# ---------------------------------------------------------------------------


def test_compute_content_hash_none_returns_none(verifier: ArtifactVerifier) -> None:
    assert verifier.compute_content_hash(None) is None


def test_compute_content_hash_empty_returns_none(verifier: ArtifactVerifier) -> None:
    assert verifier.compute_content_hash("") is None


def test_compute_content_hash_matches_sha256_of_bytes(verifier: ArtifactVerifier) -> None:
    # Must hash the literal content_ref string bytes — not decode it
    # or apply any normalisation.
    ref = "s3://bucket/path/file.py?version=42"
    expected = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    assert verifier.compute_content_hash(ref) == expected


def test_compute_content_hash_handles_unicode_and_spaces(verifier: ArtifactVerifier) -> None:
    ref = "héllo wörld 🌍"
    expected = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    assert verifier.compute_content_hash(ref) == expected


def test_compute_content_hash_stable_across_calls(verifier: ArtifactVerifier) -> None:
    ref = "/abs/path/to/file.txt"
    a = verifier.compute_content_hash(ref)
    b = verifier.compute_content_hash(ref)
    assert a == b
    assert a is not None


# ---------------------------------------------------------------------------
# verify_artifact
# ---------------------------------------------------------------------------


def test_verify_artifact_missing(verifier: ArtifactVerifier) -> None:
    result = verifier.verify_artifact("does-not-exist")
    assert result == {
        "artifact_id": "does-not-exist",
        "expected_hash": None,
        "actual_hash": None,
        "valid": False,
        "reason": "artifact_missing",
    }


def test_verify_artifact_valid_when_hashes_match(
    verifier: ArtifactVerifier,
    artifact_repo: ArtifactRepository,
    workspace_id: str,
) -> None:
    content_ref = "/tmp/code/main.py"
    art = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id="sess-1",
        artifact_kind="code",
        content_ref=content_ref,
        content_hash=hashlib.sha256(content_ref.encode("utf-8")).hexdigest(),
    )
    result = verifier.verify_artifact(art.artifact_id)
    assert result["valid"] is True
    assert result["reason"] == "ok"
    assert result["expected_hash"] == result["actual_hash"]
    assert result["expected_hash"] is not None
    assert result["artifact_id"] == art.artifact_id


def test_verify_artifact_invalid_when_hashes_mismatch(
    verifier: ArtifactVerifier,
    artifact_repo: ArtifactRepository,
    workspace_id: str,
) -> None:
    art = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id="sess-1",
        artifact_kind="code",
        content_ref="/tmp/code/main.py",
        # Wrong hash on purpose.
        content_hash="0" * 64,
    )
    result = verifier.verify_artifact(art.artifact_id)
    assert result["valid"] is False
    assert result["reason"] == "hash_mismatch"
    assert result["expected_hash"] == "0" * 64
    assert result["actual_hash"] is not None
    assert result["expected_hash"] != result["actual_hash"]


def test_verify_artifact_invalid_when_expected_hash_missing(
    verifier: ArtifactVerifier,
    artifact_repo: ArtifactRepository,
    workspace_id: str,
) -> None:
    art = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id="sess-1",
        artifact_kind="code",
        content_ref="/tmp/code/main.py",
        content_hash=None,
    )
    result = verifier.verify_artifact(art.artifact_id)
    assert result["valid"] is False
    assert result["reason"] == "expected_hash_missing"
    assert result["expected_hash"] is None
    assert result["actual_hash"] is not None


def test_verify_artifact_invalid_when_content_ref_missing(
    verifier: ArtifactVerifier,
    artifact_repo: ArtifactRepository,
    workspace_id: str,
) -> None:
    art = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id="sess-1",
        artifact_kind="code",
        content_ref=None,
        content_hash=hashlib.sha256(b"irrelevant").hexdigest(),
    )
    result = verifier.verify_artifact(art.artifact_id)
    assert result["valid"] is False
    assert result["reason"] == "actual_hash_missing"
    assert result["expected_hash"] is not None
    assert result["actual_hash"] is None


def test_verify_artifact_invalid_when_both_hashes_missing(
    verifier: ArtifactVerifier,
    artifact_repo: ArtifactRepository,
    workspace_id: str,
) -> None:
    art = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id="sess-1",
        artifact_kind="code",
        content_ref=None,
        content_hash=None,
    )
    result = verifier.verify_artifact(art.artifact_id)
    assert result["valid"] is False
    # expected_hash is checked first in the implementation.
    assert result["reason"] == "expected_hash_missing"


# ---------------------------------------------------------------------------
# verify_artifacts_for_run
# ---------------------------------------------------------------------------


def test_verify_artifacts_for_run_with_no_artifacts(
    verifier: ArtifactVerifier,
    harness_run_repo: HarnessRunRepository,
    workspace_id: str,
) -> None:
    run = harness_run_repo.create(
        workspace_id=workspace_id,
        session_id="sess-empty",
        harness_type="hermes",
    )
    summary = verifier.verify_artifacts_for_run(run.harness_run_id)
    assert summary == {
        "harness_run_id": run.harness_run_id,
        "checked_count": 0,
        "all_valid": True,
        "invalid_artifact_ids": [],
        "results": [],
    }


def test_verify_artifacts_for_run_all_valid(
    verifier: ArtifactVerifier,
    artifact_repo: ArtifactRepository,
    harness_run_repo: HarnessRunRepository,
    workspace_id: str,
) -> None:
    run = harness_run_repo.create(
        workspace_id=workspace_id,
        session_id="sess-1",
        harness_type="hermes",
    )
    refs = ["/tmp/a.py", "/tmp/b.py", "/tmp/c.py"]
    artifacts: list[Artifact] = []
    for ref in refs:
        artifacts.append(
            artifact_repo.create(
                workspace_id=workspace_id,
                producer_session_id="sess-1",
                producer_harness_run_id=run.harness_run_id,
                artifact_kind="code",
                content_ref=ref,
                content_hash=hashlib.sha256(ref.encode("utf-8")).hexdigest(),
            )
        )

    summary = verifier.verify_artifacts_for_run(run.harness_run_id)
    assert summary["harness_run_id"] == run.harness_run_id
    assert summary["checked_count"] == 3
    assert summary["all_valid"] is True
    assert summary["invalid_artifact_ids"] == []
    assert [r["artifact_id"] for r in summary["results"]] == [
        a.artifact_id for a in artifacts
    ]
    assert all(r["valid"] is True for r in summary["results"])


def test_verify_artifacts_for_run_aggregates_invalid(
    verifier: ArtifactVerifier,
    artifact_repo: ArtifactRepository,
    harness_run_repo: HarnessRunRepository,
    workspace_id: str,
) -> None:
    run = harness_run_repo.create(
        workspace_id=workspace_id,
        session_id="sess-1",
        harness_type="hermes",
    )
    valid = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id="sess-1",
        producer_harness_run_id=run.harness_run_id,
        artifact_kind="code",
        content_ref="/tmp/ok.py",
        content_hash=hashlib.sha256(b"/tmp/ok.py").hexdigest(),
    )
    bad_match = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id="sess-1",
        producer_harness_run_id=run.harness_run_id,
        artifact_kind="code",
        content_ref="/tmp/bad.py",
        content_hash="f" * 64,  # mismatch
    )
    no_expected = artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id="sess-1",
        producer_harness_run_id=run.harness_run_id,
        artifact_kind="code",
        content_ref="/tmp/no_expected.py",
        content_hash=None,
    )
    # Unrelated artifact for a different run — must not appear.
    other_run = harness_run_repo.create(
        workspace_id=workspace_id,
        session_id="sess-1",
        harness_type="shell",
    )
    artifact_repo.create(
        workspace_id=workspace_id,
        producer_session_id="sess-1",
        producer_harness_run_id=other_run.harness_run_id,
        artifact_kind="code",
        content_ref="/tmp/other.py",
        content_hash=hashlib.sha256(b"/tmp/other.py").hexdigest(),
    )

    summary = verifier.verify_artifacts_for_run(run.harness_run_id)
    assert summary["checked_count"] == 3
    assert summary["all_valid"] is False
    # Insertion order — the unrelated artifact must not leak in.
    result_ids = [r["artifact_id"] for r in summary["results"]]
    assert valid.artifact_id in result_ids
    assert bad_match.artifact_id in result_ids
    assert no_expected.artifact_id in result_ids
    assert len(result_ids) == 3

    invalid = set(summary["invalid_artifact_ids"])
    assert invalid == {bad_match.artifact_id, no_expected.artifact_id}

    # Spot-check the per-artifact reasons.
    by_id = {r["artifact_id"]: r for r in summary["results"]}
    assert by_id[bad_match.artifact_id]["reason"] == "hash_mismatch"
    assert by_id[no_expected.artifact_id]["reason"] == "expected_hash_missing"
    assert by_id[valid.artifact_id]["valid"] is True
