"""Tests for ForkService — the structured session-fork contract.

These tests cover the Phase 5 fork service:
* ``create_fork`` atomically creates a :class:`ForkRecord` and a child
  :class:`SessionExtension` with the new session type.
* Type changes go through the fork path — the repository refuses to
  mutate ``session_type`` in place, and the service uses a fresh child
  row to realise the new type.
* Validation rejects empty summaries, invalid target types, unknown
  ``initiated_by`` values, and references to non-existent parents.
* ``checkpoint_json`` is a valid versioned dict per spec §9.
* The suggestion policy is conservative: chat-like signals never
  trigger a suggestion, but explicit research/work keywords do.
* Fork context is stored in the product-layer ``workbench.db`` (via
  ``workbench.db`` schema), not in any Hermes-only table.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from agent_workbench.models.fork_record import (
    ForkRecord,
    ForkRecordRepository,
)
from agent_workbench.models.session_extension import (
    SessionExtension,
    SessionExtensionRepository,
)
from agent_workbench.models.workspace import Workspace, WorkspaceRepository
from agent_workbench.services.fork_service import ForkService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_id(db: sqlite3.Connection) -> str:
    """Provide a fresh workspace for each test."""
    repo = WorkspaceRepository(db)
    ws: Workspace = repo.create(tenant_id="tenant-1", name="Test Workspace")
    return ws.workspace_id


@pytest.fixture
def fork_service(db: sqlite3.Connection) -> ForkService:
    return ForkService(db)


@pytest.fixture
def session_repo(db: sqlite3.Connection) -> SessionExtensionRepository:
    return SessionExtensionRepository(db)


@pytest.fixture
def fork_repo(db: sqlite3.Connection) -> ForkRecordRepository:
    return ForkRecordRepository(db)


def _make_parent(
    session_repo: SessionExtensionRepository,
    workspace_id: str,
    session_type: str = "chat",
) -> SessionExtension:
    return session_repo.create(
        workspace_id=workspace_id,
        session_type=session_type,
    )


# ---------------------------------------------------------------------------
# create_fork
# ---------------------------------------------------------------------------


class TestCreateFork:
    def test_creates_fork_record_and_child_session(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        fork_repo: ForkRecordRepository,
        db: sqlite3.Connection,
        workspace_id: str,
    ) -> None:
        """A fork must produce both a ForkRecord and a child SessionExtension."""
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        child_session_id = "child-session-abc"

        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id=child_session_id,
            new_session_type="research",
            fork_reason="dig deeper into the topic",
            initiated_by="user",
            summary="User asked about transformer attention mechanisms.",
        )

        # ForkRecord persisted.
        assert isinstance(record, ForkRecord)
        assert record.parent_session_id == parent.session_id
        assert record.child_session_id == child_session_id
        assert record.fork_reason == "dig deeper into the topic"
        assert record.initiated_by == "user"
        assert record.summary_ref == (
            "User asked about transformer attention mechanisms."
        )

        # ForkRecord re-readable from DB.
        roundtripped = fork_repo.get_by_id(record.fork_id)
        assert roundtripped is not None
        assert roundtripped.fork_id == record.fork_id

        # Child SessionExtension persisted with the new type and fork link.
        child = session_repo.get_by_id(child_session_id)
        assert child is not None
        assert child.session_type == "research"
        assert child.fork_id == record.fork_id
        assert child.workspace_id == workspace_id
        assert child.status == "active"
        channel = db.execute(
            "SELECT channel_kind, active_session_id FROM channels "
            "WHERE active_session_id = ?",
            (child_session_id,),
        ).fetchone()
        assert channel is not None
        assert channel["channel_kind"] == "research"

    def test_branch_kind_when_type_is_unchanged(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-branch",
            new_session_type="chat",
            fork_reason="exploring alternate path",
            initiated_by="user",
            summary="Branching off to try a different angle.",
        )
        assert record.fork_kind == "branch"

    def test_type_change_kind_inferred(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-typechange",
            new_session_type="work",
            fork_reason="chat became implementation work",
            initiated_by="orchestrator",
            summary="Concrete deliverables are now in scope.",
        )
        assert record.fork_kind == "type_change"

    def test_bootstrap_context_role_defaults_to_fork_context(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-bootstrap",
            new_session_type="research",
            fork_reason="r",
            initiated_by="user",
            summary="Investigating topic X.",
        )
        assert record.bootstrap_context_role_internal == "fork_context"

    def test_structured_payloads_persisted_as_json(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        fork_repo: ForkRecordRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        decisions = {"picked": "transformer", "rejected": "rnn"}
        assumptions = {"data_clean": True}
        open_questions = ["q1", "q2"]
        artifacts = {"spec_doc": "art-1"}

        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-payloads",
            new_session_type="research",
            fork_reason="r",
            initiated_by="user",
            summary="Summary text.",
            decisions=decisions,
            assumptions=assumptions,
            open_questions=open_questions,
            relevant_artifacts=artifacts,
        )

        # Re-read from DB to confirm JSON round-trip.
        roundtripped = fork_repo.get_by_id(record.fork_id)
        assert roundtripped is not None
        assert roundtripped.decisions_json == decisions
        assert roundtripped.assumptions_json == assumptions
        assert roundtripped.open_questions_json == open_questions
        assert roundtripped.relevant_artifacts_json == artifacts

    def test_child_session_uses_parent_workspace(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-ws",
            new_session_type="research",
            fork_reason="r",
            initiated_by="user",
            summary="Inherits parent workspace.",
        )
        child = session_repo.get_by_id("child-ws")
        assert child is not None
        assert child.workspace_id == parent.workspace_id
        assert child.workspace_id == workspace_id
        assert record.parent_session_id == parent.session_id

    def test_fork_context_stored_product_side(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        fork_repo: ForkRecordRepository,
        db: sqlite3.Connection,
        workspace_id: str,
    ) -> None:
        """Fork context lives in workbench.db, not in any external store."""
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-product-side",
            new_session_type="research",
            fork_reason="r",
            initiated_by="user",
            summary="Persisted in product-layer table.",
        )

        # Verify rows are present in the product-layer tables.
        fork_row = db.execute(
            "SELECT fork_id FROM fork_records WHERE fork_id = ?",
            (record.fork_id,),
        ).fetchone()
        assert fork_row is not None

        ext_row = db.execute(
            "SELECT session_id FROM session_extensions WHERE session_id = ?",
            ("child-product-side",),
        ).fetchone()
        assert ext_row is not None

        # Verify that the same data is reachable via the repositories,
        # proving it is stored inside workbench.db rather than some
        # Hermes-only sidecar.
        assert fork_repo.get_by_id(record.fork_id) is not None
        assert session_repo.get_by_id("child-product-side") is not None


# ---------------------------------------------------------------------------
# Type-change requires a fork (cannot mutate session_type directly)
# ---------------------------------------------------------------------------


class TestTypeChangeRequiresFork:
    def test_session_extension_repository_refuses_type_mutation(
        self,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        """The repository exposes no method to update session_type at all.

        This is the repository-level invariant the service relies on.
        """
        ext = session_repo.create(
            workspace_id=workspace_id,
            session_type="chat",
        )
        # The only mutators in SessionExtensionRepository are
        # update_status and update_task_spec — neither touches
        # session_type. Calling them must not change the type.
        session_repo.update_status(ext.session_id, status="done")
        session_repo.update_task_spec(ext.session_id, task_spec_id=None)
        refetched = session_repo.get_by_id(ext.session_id)
        assert refetched is not None
        assert refetched.session_type == "chat"

    def test_type_change_realised_via_new_child_not_mutation(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-typed-change",
            new_session_type="work",
            fork_reason="r",
            initiated_by="user",
            summary="Switching to a work lane.",
        )

        # Parent is untouched.
        refetched_parent = session_repo.get_by_id(parent.session_id)
        assert refetched_parent is not None
        assert refetched_parent.session_type == "chat"
        assert refetched_parent.fork_id is None

        # The new type lives on the child, not the parent.
        child = session_repo.get_by_id(record.child_session_id)
        assert child is not None
        assert child.session_type == "work"
        assert child.fork_id == record.fork_id
        assert record.fork_kind == "type_change"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_empty_summary_rejected(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        with pytest.raises(ValueError, match="summary"):
            fork_service.create_fork(
                parent_session_id=parent.session_id,
                child_session_id="child-empty",
                new_session_type="research",
                fork_reason="r",
                initiated_by="user",
                summary="",
            )

    def test_whitespace_only_summary_rejected(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        with pytest.raises(ValueError, match="summary"):
            fork_service.create_fork(
                parent_session_id=parent.session_id,
                child_session_id="child-ws-summary",
                new_session_type="research",
                fork_reason="r",
                initiated_by="user",
                summary="   \t\n  ",
            )

    def test_invalid_session_type_rejected(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        with pytest.raises(ValueError, match="Invalid session_type"):
            fork_service.create_fork(
                parent_session_id=parent.session_id,
                child_session_id="child-bad-type",
                new_session_type="bogus",
                fork_reason="r",
                initiated_by="user",
                summary="Valid summary.",
            )

    def test_nonexistent_parent_rejected(
        self,
        fork_service: ForkService,
    ) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            fork_service.create_fork(
                parent_session_id="ghost-parent",
                child_session_id="child-orphan",
                new_session_type="research",
                fork_reason="r",
                initiated_by="user",
                summary="Valid summary.",
            )

    def test_invalid_initiated_by_rejected(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        with pytest.raises(ValueError, match="initiated_by"):
            fork_service.create_fork(
                parent_session_id=parent.session_id,
                child_session_id="child-bad-init",
                new_session_type="research",
                fork_reason="r",
                initiated_by="wizard",
                summary="Valid summary.",
            )

    def test_validation_runs_before_any_db_write(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        fork_repo: ForkRecordRepository,
        workspace_id: str,
    ) -> None:
        """Failed validation must not leave a partial fork record behind."""
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        forks_before = len(fork_repo.get_by_parent_session(parent.session_id))
        with pytest.raises(ValueError):
            fork_service.create_fork(
                parent_session_id=parent.session_id,
                child_session_id="child-should-not-exist",
                new_session_type="bogus",
                fork_reason="r",
                initiated_by="user",
                summary="Valid summary.",
            )
        forks_after = len(fork_repo.get_by_parent_session(parent.session_id))
        assert forks_after == forks_before
        assert session_repo.get_by_id("child-should-not-exist") is None


# ---------------------------------------------------------------------------
# checkpoint_json
# ---------------------------------------------------------------------------


class TestCheckpointJson:
    def test_checkpoint_is_versioned_dict(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-checkpoint",
            new_session_type="research",
            fork_reason="r",
            initiated_by="user",
            summary="Check checkpoint structure.",
        )

        checkpoint = record.checkpoint_json
        assert isinstance(checkpoint, dict)
        assert checkpoint["version"] == 1
        assert checkpoint["source_session_id"] == parent.session_id
        assert checkpoint["source_message_offset"] == 0

    def test_checkpoint_serialized_as_valid_json_in_db(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        db: sqlite3.Connection,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-json",
            new_session_type="research",
            fork_reason="r",
            initiated_by="user",
            summary="Check JSON serialisation.",
        )

        row = db.execute(
            "SELECT checkpoint_json FROM fork_records WHERE fork_id = ?",
            (record.fork_id,),
        ).fetchone()
        assert row is not None
        raw = row["checkpoint_json"]
        # The on-disk representation is a JSON string that round-trips
        # to the same dict as the dataclass field.
        assert isinstance(raw, str)
        parsed = json.loads(raw)
        assert parsed == record.checkpoint_json
        assert parsed["version"] == 1
        assert parsed["source_session_id"] == parent.session_id


# ---------------------------------------------------------------------------
# get_fork / get_forks_by_parent / get_forks_by_child
# ---------------------------------------------------------------------------


class TestGetForks:
    def test_get_fork_returns_record(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-get",
            new_session_type="research",
            fork_reason="r",
            initiated_by="user",
            summary="Lookup by id.",
        )
        fetched = fork_service.get_fork(record.fork_id)
        assert fetched.fork_id == record.fork_id
        assert fetched.parent_session_id == parent.session_id

    def test_get_fork_missing_raises(
        self,
        fork_service: ForkService,
    ) -> None:
        with pytest.raises(LookupError):
            fork_service.get_fork("nonexistent-fork")

    def test_get_forks_by_parent(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-a",
            new_session_type="research",
            fork_reason="r1",
            initiated_by="user",
            summary="First fork.",
        )
        fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-b",
            new_session_type="work",
            fork_reason="r2",
            initiated_by="orchestrator",
            summary="Second fork.",
        )
        results = fork_service.get_forks_by_parent(parent.session_id)
        assert len(results) == 2
        assert {r.child_session_id for r in results} == {"child-a", "child-b"}
        # No leakage from other parents.
        other_parent = _make_parent(session_repo, workspace_id, session_type="chat")
        assert fork_service.get_forks_by_parent(other_parent.session_id) == []

    def test_get_forks_by_child(
        self,
        fork_service: ForkService,
        session_repo: SessionExtensionRepository,
        workspace_id: str,
    ) -> None:
        parent = _make_parent(session_repo, workspace_id, session_type="chat")
        record = fork_service.create_fork(
            parent_session_id=parent.session_id,
            child_session_id="child-only",
            new_session_type="research",
            fork_reason="r",
            initiated_by="user",
            summary="Single fork.",
        )
        fetched = fork_service.get_forks_by_child("child-only")
        assert fetched.fork_id == record.fork_id

    def test_get_forks_by_child_missing_raises(
        self,
        fork_service: ForkService,
    ) -> None:
        with pytest.raises(LookupError):
            fork_service.get_forks_by_child("nonexistent-child")


# ---------------------------------------------------------------------------
# Conservative suggestion policy
# ---------------------------------------------------------------------------


class TestSuggestForkIfNeeded:
    def test_no_suggestion_for_chat_signals(
        self,
        fork_service: ForkService,
    ) -> None:
        chat_signals = [
            "hi there",
            "thanks!",
            "how are you?",
            "ok",
            "yes please",
        ]
        assert fork_service.suggest_fork_if_needed("sess-1", chat_signals) is None

    def test_suggestion_for_research_signal(
        self,
        fork_service: ForkService,
    ) -> None:
        result = fork_service.suggest_fork_if_needed(
            "sess-1",
            ["we should research transformer architectures"],
        )
        assert result is not None
        assert result["session_id"] == "sess-1"
        assert result["suggested_session_type"] == "research"
        assert "research" in result["matched_keywords"]

    def test_suggestion_for_work_signal(
        self,
        fork_service: ForkService,
    ) -> None:
        result = fork_service.suggest_fork_if_needed(
            "sess-1",
            ["let's implement a REST endpoint"],
        )
        assert result is not None
        assert result["suggested_session_type"] == "work"
        assert "implement" in result["matched_keywords"]

    def test_suggestion_for_investigate_signal(
        self,
        fork_service: ForkService,
    ) -> None:
        result = fork_service.suggest_fork_if_needed(
            "sess-1",
            ["please investigate this bug"],
        )
        assert result is not None
        assert result["suggested_session_type"] == "research"
        assert "investigate" in result["matched_keywords"]

    def test_suggestion_for_implement_signal(
        self,
        fork_service: ForkService,
    ) -> None:
        result = fork_service.suggest_fork_if_needed(
            "sess-1",
            ["we need to implement caching"],
        )
        assert result is not None
        assert result["suggested_session_type"] == "work"
        assert "implement" in result["matched_keywords"]

    def test_suggestion_is_advisory_no_db_write(
        self,
        fork_service: ForkService,
        fork_repo: ForkRecordRepository,
    ) -> None:
        """suggest_fork_if_needed must never write to the database."""
        before = len(fork_repo.list_by_kind("branch"))
        fork_service.suggest_fork_if_needed(
            "sess-1",
            ["we should research transformer architectures"],
        )
        fork_service.suggest_fork_if_needed(
            "sess-1",
            ["hi", "thanks"],
        )
        after = len(fork_repo.list_by_kind("branch"))
        assert before == after

    def test_no_suggestion_for_empty_signals(
        self,
        fork_service: ForkService,
    ) -> None:
        assert fork_service.suggest_fork_if_needed("sess-1", []) is None
        assert fork_service.suggest_fork_if_needed("sess-1", ["", "  "]) is None

    def test_suggestion_ignores_non_string_signals(
        self,
        fork_service: ForkService,
    ) -> None:
        # Mixed-type iterable: only strings are scanned.
        result = fork_service.suggest_fork_if_needed(
            "sess-1",
            [None, 42, "research this", {"ignore": "me"}],
        )
        assert result is not None
        assert "research" in result["matched_keywords"]

    def test_work_beats_research_when_both_signalled(
        self,
        fork_service: ForkService,
    ) -> None:
        # Both research and work keywords present -> work wins.
        result = fork_service.suggest_fork_if_needed(
            "sess-1",
            ["let's research and then implement the algorithm"],
        )
        assert result is not None
        assert result["suggested_session_type"] == "work"
        # Both keywords must be reported.
        assert set(result["matched_keywords"]) >= {"research", "implement"}


# ---------------------------------------------------------------------------
# Constructor & wiring
# ---------------------------------------------------------------------------


class TestServiceWiring:
    def test_constructor_accepts_connection(
        self,
        db: sqlite3.Connection,
    ) -> None:
        service = ForkService(db)
        assert service.conn is db
        assert service.fork_repo is not None
        assert service.session_repo is not None

    def test_service_importable_from_package(
        self,
    ) -> None:
        from agent_workbench.services import ForkService as Imported

        assert Imported is ForkService
