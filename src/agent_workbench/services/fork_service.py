"""ForkService — structured fork contract for the Agent Workbench.

This service implements the product-layer session forking workflow defined in
``04_SESSION_FORKING.md`` and ``03_DOMAIN_MODEL.md``.

Key responsibilities
--------------------
* Validate the structured fork payload (parent exists, target type valid,
  summary non-empty, ``initiated_by`` recognised).
* Create the child :class:`SessionExtension` in the same workspace as the
  parent, linked back to the new :class:`ForkRecord` via ``fork_id``.
* Auto-generate a versioned ``checkpoint_json`` and stamp the standard
  ``bootstrap_context_role_internal = "fork_context"``.
* Expose a conservative suggestion policy that never silently mutates a
  session type — the user or orchestrator always has to act.

Design notes
------------
* Fork metadata lives in the product-layer ``fork_records`` table inside
  ``workbench.db``. It is **not** stored in the Hermes session tables
  (see open_decision #15 and spec §4).
* The ``session_type`` field on :class:`SessionExtension` is immutable at
  the repository level (see :class:`SessionExtensionRepository`); the
  service therefore creates a new child session for every type transition
  rather than mutating the parent in place.
* Atomicity: the service validates the entire payload up front, then
  creates the :class:`ForkRecord` followed by the child
  :class:`SessionExtension`. If the child insert fails the caller will
  see the exception; the orphan :class:`ForkRecord` is left in place so
  a follow-up cleanup pass can reconcile state.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

from agent_workbench.models.fork_record import (
    ForkRecord,
    ForkRecordRepository,
)
from agent_workbench.models.session_extension import (
    SESSION_STATUSES,
    SESSION_TYPES,
    SessionExtension,
    SessionExtensionRepository,
)


# Matches the CHECK constraint on fork_records.initiated_by.
_VALID_INITIATED_BY = ("user", "orchestrator", "system")

# Fork-kind values declared by the schema. The service infers
# ``type_change`` automatically when the parent and target types differ.
_FORK_KIND_BRANCH = "branch"
_FORK_KIND_TYPE_CHANGE = "type_change"
_FORK_KIND_REPLAY = "replay"
_FORK_KIND_RETRY = "retry"

# Conservative trigger keywords for the suggestion policy. These mirror
# the language used in the "research / work" lane terminology in spec
# §2. Chat signals (e.g. "hi", "thanks") never trigger a suggestion.
_SUGGEST_FORK_KEYWORDS = (
    "research",
    "work",
    "investigate",
    "implement",
    "build",
    "develop",
    "explore",
    "analyze",
    "design",
    "prototype",
)

# Mapping from explicit trigger keywords to the target session_type a
# fork should create. ``None`` means "let the caller decide" — the
# suggestion policy only flags the *opportunity*, not the destination.
_KEYWORD_TO_TYPE_HINT: Dict[str, Optional[str]] = {
    "research": "research",
    "investigate": "research",
    "analyze": "research",
    "explore": "research",
    "work": "work",
    "implement": "work",
    "build": "work",
    "develop": "work",
    "design": "work",
    "prototype": "work",
}

_DEFAULT_BOOTSTRAP_CONTEXT_ROLE = "fork_context"
_CHECKPOINT_VERSION = 1
_DEFAULT_SOURCE_MESSAGE_OFFSET = 0


class ForkService:
    """Service for creating and inspecting structured session forks.

    The service wraps :class:`ForkRecordRepository` and
    :class:`SessionExtensionRepository` and adds the product-layer
    validation, inference, and policy rules that the repositories alone
    cannot enforce.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.fork_repo = ForkRecordRepository(conn)
        self.session_repo = SessionExtensionRepository(conn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_fork(
        self,
        parent_session_id: str,
        child_session_id: str,
        new_session_type: str,
        fork_reason: str,
        initiated_by: str,
        summary: str = "",
        decisions: Optional[Dict[str, Any]] = None,
        assumptions: Optional[Dict[str, Any]] = None,
        open_questions: Optional[Dict[str, Any]] = None,
        relevant_artifacts: Optional[Dict[str, Any]] = None,
    ) -> ForkRecord:
        """Create a structured fork, including the child session extension.

        Parameters
        ----------
        parent_session_id:
            ``session_id`` of the existing session to fork from. Must
            reference a row in :class:`SessionExtensionRepository`.
        child_session_id:
            Caller-supplied ``session_id`` for the new child session. In
            practice this is the id the product has already allocated
            (or reserved) in the upstream session store.
        new_session_type:
            Target session type for the child. Must be one of
            ``chat``, ``research``, ``work``. When this differs from the
            parent's type the fork is recorded as
            ``fork_kind = 'type_change'``; otherwise ``'branch'``.
        fork_reason:
            Free-text human-readable explanation of why the fork was
            created. Stored verbatim on the :class:`ForkRecord`.
        initiated_by:
            Origin of the fork request. Must be one of ``user``,
            ``orchestrator``, ``system``.
        summary:
            Required non-empty structured summary of the parent session
            at the point of the fork. Used as the primary inheritance
            payload.
        decisions, assumptions, open_questions, relevant_artifacts:
            Optional structured inheritance payloads. Serialized to
            JSON via :func:`json.dumps` before persistence.

        Returns
        -------
        ForkRecord
            The persisted fork record, with its generated ``fork_id``
            and timestamps.

        Raises
        ------
        ValueError
            If the parent session does not exist, the target session
            type is invalid, ``initiated_by`` is not recognised, or
            ``summary`` is empty / whitespace.
        """
        self._validate_parent(parent_session_id)
        self._validate_session_type(new_session_type)
        self._validate_initiated_by(initiated_by)
        self._validate_summary(summary)

        parent = self.session_repo.get_by_id(parent_session_id)
        # ``parent`` is guaranteed non-None by ``_validate_parent``.
        assert parent is not None

        fork_kind = (
            _FORK_KIND_TYPE_CHANGE
            if new_session_type != parent.session_type
            else _FORK_KIND_BRANCH
        )

        checkpoint = self._build_checkpoint(parent_session_id)

        # Persist the fork record first so the child SessionExtension
        # can reference it via the ``fork_id`` foreign key. Validation
        # has already happened above, so a failure here surfaces as an
        # exception to the caller and leaves no partial state.
        fork_record = self.fork_repo.create(
            parent_session_id=parent_session_id,
            child_session_id=child_session_id,
            fork_kind=fork_kind,
            fork_reason=fork_reason,
            initiated_by=initiated_by,
            summary_ref=summary,
            decisions_json=decisions,
            assumptions_json=assumptions,
            open_questions_json=open_questions,
            relevant_artifacts_json=relevant_artifacts,
            bootstrap_context_role_internal=_DEFAULT_BOOTSTRAP_CONTEXT_ROLE,
            checkpoint_json=checkpoint,
        )

        # Create the child SessionExtension in the same workspace,
        # stamped with the new type and back-linked to the fork. The
        # session_id comes from the caller (typically a Hermes session
        # id the product has already allocated), so we insert directly
        # rather than going through SessionExtensionRepository.create,
        # which always generates a fresh UUID.
        self._create_child_session_extension(
            session_id=child_session_id,
            workspace_id=parent.workspace_id,
            session_type=new_session_type,
            fork_id=fork_record.fork_id,
        )

        return fork_record

    def get_fork(self, fork_id: str) -> ForkRecord:
        """Return the fork record with the given id.

        Raises
        ------
        LookupError
            If no fork with that id exists.
        """
        record = self.fork_repo.get_by_id(fork_id)
        if record is None:
            raise LookupError(f"No fork record found with id {fork_id!r}")
        return record

    def get_forks_by_parent(self, parent_session_id: str) -> List[ForkRecord]:
        """Return all fork records whose parent is ``parent_session_id``."""
        return list(self.fork_repo.get_by_parent_session(parent_session_id))

    def get_forks_by_child(self, child_session_id: str) -> ForkRecord:
        """Return the fork record that produced ``child_session_id``.

        The schema permits at most one fork per child session, so this
        returns a single :class:`ForkRecord` rather than a list.

        Raises
        ------
        LookupError
            If no fork produced that child session.
        """
        record = self.fork_repo.get_by_child_session(child_session_id)
        if record is None:
            raise LookupError(
                f"No fork record found for child session {child_session_id!r}"
            )
        return record

    # ------------------------------------------------------------------
    # Suggestion policy
    # ------------------------------------------------------------------

    def suggest_fork_if_needed(
        self,
        session_id: str,
        signals: Iterable[str],
    ) -> Optional[Dict[str, Any]]:
        """Conservative fork suggestion.

        Returns a suggestion dict only when the supplied ``signals``
        contain at least one explicit project/research/work keyword
        (e.g. ``"research"``, ``"work"``, ``"investigate"``,
        ``"implement"``). Chat-like signals (greetings, thanks,
        small-talk) never produce a suggestion.

        The returned dict is **advisory only**; the caller is expected
        to surface it to the user or orchestrator and let them
        explicitly trigger a fork via :meth:`create_fork`. The service
        itself never auto-creates forks on the strength of signals.

        Parameters
        ----------
        session_id:
            The session whose signals are being evaluated. The service
            does not mutate it — included only so the caller can render
            the suggestion in the right context.
        signals:
            An iterable of free-text fragments (recent user messages,
            intent labels, tool outputs, …). Matching is case-folded
            and substring-based.

        Returns
        -------
        Optional[dict]
            ``None`` when no action is suggested. Otherwise a dict with
            keys ``session_id``, ``suggested_session_type``,
            ``matched_keywords``, and ``reason``.
        """
        if not signals:
            return None

        matched: List[str] = []
        for raw in signals:
            if not isinstance(raw, str):
                continue
            token = raw.strip().lower()
            if not token:
                continue
            for keyword in _SUGGEST_FORK_KEYWORDS:
                if keyword in token and keyword not in matched:
                    matched.append(keyword)

        if not matched:
            return None

        # Pick the highest-priority target type hinted at by the
        # matched keywords. ``work`` wins over ``research`` when both
        # are present because the spec treats work as the strictly
        # stronger lane.
        target_type: Optional[str] = None
        for keyword in matched:
            hint = _KEYWORD_TO_TYPE_HINT.get(keyword)
            if hint == "work":
                target_type = "work"
                break
            if hint == "research" and target_type is None:
                target_type = "research"

        return {
            "session_id": session_id,
            "suggested_session_type": target_type,
            "matched_keywords": matched,
            "reason": (
                "Conservative fork suggestion: detected explicit "
                "project/research/work signal(s) in session activity."
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_parent(self, parent_session_id: str) -> None:
        if not parent_session_id:
            raise ValueError("parent_session_id must not be empty")
        parent = self.session_repo.get_by_id(parent_session_id)
        if parent is None:
            raise ValueError(
                f"Parent session {parent_session_id!r} does not exist"
            )

    @staticmethod
    def _validate_session_type(session_type: str) -> None:
        if session_type not in SESSION_TYPES:
            raise ValueError(
                f"Invalid session_type: {session_type!r}. "
                f"Must be one of {SESSION_TYPES}"
            )

    @staticmethod
    def _validate_initiated_by(initiated_by: str) -> None:
        if initiated_by not in _VALID_INITIATED_BY:
            raise ValueError(
                f"Invalid initiated_by: {initiated_by!r}. "
                f"Must be one of {_VALID_INITIATED_BY}"
            )

    @staticmethod
    def _validate_summary(summary: str) -> None:
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary must be a non-empty string")

    @staticmethod
    def _build_checkpoint(source_session_id: str) -> Dict[str, Any]:
        """Build the versioned checkpoint dict per spec §9.

        The shape is intentionally minimal: ``version``,
        ``source_session_id`` and ``source_message_offset``. The
        optional fields (``token_position``, ``prompt_hash``,
        ``artifact_refs``) are reserved for future checkpoint
        enrichments and are not emitted yet.
        """
        return {
            "version": _CHECKPOINT_VERSION,
            "source_session_id": source_session_id,
            "source_message_offset": _DEFAULT_SOURCE_MESSAGE_OFFSET,
        }

    def _create_child_session_extension(
        self,
        *,
        session_id: str,
        workspace_id: str,
        session_type: str,
        fork_id: str,
        status: str = "active",
    ) -> None:
        """Insert a child :class:`SessionExtension` row with a caller-supplied
        ``session_id``.

        :class:`SessionExtensionRepository.create` always mints a fresh
        UUID for ``session_id``, but the fork contract needs the child
        ``session_id`` on the fork payload to match the actual session
        row. The fork service therefore performs a direct INSERT here,
        reusing the schema-level CHECK constraints (and the service's
        own pre-validated ``session_type`` / ``status`` values) to keep
        behaviour consistent with the repository.
        """
        if not session_id:
            raise ValueError("child session_id must not be empty")
        # ``session_type`` and ``status`` were already validated by the
        # public entry point, but the CHECK constraints will catch any
        # bypassed call as well.
        if session_type not in SESSION_TYPES:
            raise ValueError(
                f"Invalid session_type: {session_type!r}. "
                f"Must be one of {SESSION_TYPES}"
            )
        if status not in SESSION_STATUSES:
            raise ValueError(
                f"Invalid status: {status!r}. Must be one of {SESSION_STATUSES}"
            )
        created_at = time.time()
        self.conn.execute(
            "INSERT INTO session_extensions "
            "(session_id, workspace_id, session_type, agent_profile_binding_id, "
            "fork_id, task_spec_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                workspace_id,
                session_type,
                None,
                fork_id,
                None,
                status,
                created_at,
            ),
        )
        self.conn.commit()


# ----------------------------------------------------------------------
# Re-exported helpers
# ----------------------------------------------------------------------

__all__ = ["ForkService"]


# The unused imports below are kept available for downstream services
# that may want to introspect the service's repository dependencies
# without re-importing the model modules.
_ = (ForkRecord, SessionExtension, json)
