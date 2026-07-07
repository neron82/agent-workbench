"""Unit tests for ``RunService`` — capability gate, TaskSpec gate, dispatch.

These tests cover the *service layer* directly (no Flask client).  The
web-layer integration is covered by ``test_runs_start_endpoint.py``.
"""
from __future__ import annotations

import sqlite3
import textwrap

import pytest

from agent_workbench.adapters.base import HarnessNotReadyError
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.task_spec import TaskSpecRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.run_service import (
    DISABLED_HARNESS_TYPES,
    HarnessUnavailableError,
    LIVE_HARNESS_TYPES,
    RunService,
    TaskSpecGateError,
)


def _make_workspace(db: sqlite3.Connection) -> str:
    ws = WorkspaceRepository(db).create(tenant_id="t", name="ws")
    return ws.workspace_id


def _make_session(db: sqlite3.Connection, workspace_id: str, *, session_type: str = "chat"):
    repo = SessionExtensionRepository(db)
    return repo.create(
        workspace_id=workspace_id,
        session_type=session_type,
        status="active",
    )


def _make_spec(db: sqlite3.Connection, workspace_id: str, *, status: str = "draft") -> str:
    spec = TaskSpecRepository(db).create(
        workspace_id=workspace_id,
        objective="refactor the auth layer",
        approval_status=status,
    )
    return spec.task_spec_id


# ---------------------------------------------------------------------------
# Capability / availability surface
# ---------------------------------------------------------------------------


class TestAvailableHarnessTypes:
    def test_live_harness_types_are_listed_first(self):
        items = RunService.available_harness_types()
        live = [i for i in items if i["live"] == "true"]
        assert live == [
            {"harness_type": "shell", "label": "shell", "live": "true"},
            {"harness_type": "opencode", "label": "opencode", "live": "true"},
            {"harness_type": "ssh", "label": "ssh", "live": "true"},
            {"harness_type": "hermes", "label": "hermes", "live": "true"},
        ]

    def test_disabled_types_carry_a_precise_reason(self):
        items = RunService.available_harness_types()
        disabled = {i["harness_type"]: i for i in items if i["live"] != "true"}
        for ht in ("discussion",):
            assert ht in disabled
            assert "reason" in disabled[ht]
            assert disabled[ht]["reason"]  # non-empty precise reason

    def test_hermes_is_listed_as_live(self):
        items = RunService.available_harness_types()
        hermes = next(i for i in items if i["harness_type"] == "hermes")
        assert hermes == {"harness_type": "hermes", "label": "hermes", "live": "true"}


# ---------------------------------------------------------------------------
# Disabled harnesses are explicitly refused
# ---------------------------------------------------------------------------


class TestDisabledHarnessTypesRefused:
    @pytest.mark.parametrize("harness_type", sorted(DISABLED_HARNESS_TYPES.keys()))
    def test_disabled_harness_raises_unavailable(self, db, harness_type):
        ws = _make_workspace(db)
        sess = _make_session(db, ws)
        svc = RunService(db)
        with pytest.raises(HarnessUnavailableError) as ei:
            svc.start_for_session(
                session_id=sess.session_id,
                harness_type=harness_type,
                command="echo hello",
            )
        # The exception message must carry the precise reason (German).
        assert "kein Prozess" in str(ei.value) or "nicht startbar" in str(ei.value)


# ---------------------------------------------------------------------------
# Unknown harness type
# ---------------------------------------------------------------------------


class TestUnknownHarnessType:
    def test_unknown_harness_raises_value_error(self, db):
        ws = _make_workspace(db)
        sess = _make_session(db, ws)
        with pytest.raises(ValueError):
            RunService(db).start_for_session(
                session_id=sess.session_id,
                harness_type="hypothetical",
                command="echo hello",
            )


# ---------------------------------------------------------------------------
# Session / preflight checks
# ---------------------------------------------------------------------------


class TestSessionAndPreflight:
    def test_missing_session_raises(self, db):
        with pytest.raises(HarnessNotReadyError):
            RunService(db).start_for_session(
                session_id="nonexistent",
                harness_type="shell",
                command="echo hi",
            )

    def test_shell_requires_command(self, db):
        ws = _make_workspace(db)
        sess = _make_session(db, ws)
        with pytest.raises(HarnessUnavailableError):
            RunService(db).start_for_session(
                session_id=sess.session_id,
                harness_type="shell",
                command="",
            )

    def test_ssh_requires_remote_host(self, db):
        ws = _make_workspace(db)
        sess = _make_session(db, ws)
        with pytest.raises(HarnessUnavailableError):
            RunService(db).start_for_session(
                session_id=sess.session_id,
                harness_type="ssh",
                command="echo hi",
                remote_host="",
            )

    def test_opencode_requires_binary(self, db, monkeypatch):
        ws = _make_workspace(db)
        sess = _make_session(db, ws)
        # Force shutil.which to return None to simulate "binary not in PATH".
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", lambda *_a, **_kw: None)
        # Also need to patch the import inside the service module.
        import agent_workbench.services.run_service as rs
        monkeypatch.setattr(rs.shutil, "which", lambda *_a, **_kw: None)
        with pytest.raises(HarnessUnavailableError) as ei:
            RunService(db).start_for_session(
                session_id=sess.session_id,
                harness_type="opencode",
                command="echo hi",
            )
        assert "Opencode-Binary" in str(ei.value)

    def test_hermes_requires_binary(self, db, monkeypatch):
        ws = _make_workspace(db)
        sess = _make_session(db, ws)
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", lambda *_a, **_kw: None)
        import agent_workbench.services.run_service as rs
        monkeypatch.setattr(rs.shutil, "which", lambda *_a, **_kw: None)
        with pytest.raises(HarnessUnavailableError) as ei:
            RunService(db).start_for_session(
                session_id=sess.session_id,
                harness_type="hermes",
                command="echo hi",
            )
        assert "Hermes-Binary" in str(ei.value)


# ---------------------------------------------------------------------------
# TaskSpec approval gate
# ---------------------------------------------------------------------------


class TestTaskSpecGate:
    def test_work_session_requires_approved_spec(self, db):
        ws = _make_workspace(db)
        sess = _make_session(db, ws, session_type="work")
        spec_id = _make_spec(db, ws, status="draft")
        with pytest.raises(TaskSpecGateError):
            RunService(db).start_for_session(
                session_id=sess.session_id,
                harness_type="shell",
                command="echo hi",
                task_spec_id=spec_id,
            )

    def test_work_session_force_bypasses_gate(self, db):
        ws = _make_workspace(db)
        sess = _make_session(db, ws, session_type="work")
        spec_id = _make_spec(db, ws, status="draft")
        run = RunService(db).start_for_session(
            session_id=sess.session_id,
            harness_type="shell",
            command="echo hi",
            task_spec_id=spec_id,
            force=True,
        )
        assert run is not None
        assert run.task_spec_id == spec_id

    def test_chat_session_does_not_require_approval(self, db):
        # For chat/research sessions, an unapproved spec is allowed;
        # the gate only fires for work-sessions.
        ws = _make_workspace(db)
        sess = _make_session(db, ws, session_type="chat")
        spec_id = _make_spec(db, ws, status="draft")
        run = RunService(db).start_for_session(
            session_id=sess.session_id,
            harness_type="shell",
            command="echo hi",
            task_spec_id=spec_id,
        )
        assert run.task_spec_id == spec_id

    def test_cross_workspace_spec_is_rejected(self, db):
        ws_a = _make_workspace(db)
        ws_b = _make_workspace(db)
        sess = _make_session(db, ws_a)
        # Spec belongs to a different workspace.
        spec_id = _make_spec(db, ws_b, status="approved")
        with pytest.raises(HarnessNotReadyError):
            RunService(db).start_for_session(
                session_id=sess.session_id,
                harness_type="shell",
                command="echo hi",
                task_spec_id=spec_id,
            )


# ---------------------------------------------------------------------------
# Real start — happy path (shell, since it has no external binary)
# ---------------------------------------------------------------------------


class TestLiveShellStart:
    def test_shell_run_starts_and_persists(self, db):
        ws = _make_workspace(db)
        sess = _make_session(db, ws)
        run = RunService(db).start_for_session(
            session_id=sess.session_id,
            harness_type="shell",
            command="echo run-service-shell-marker",
        )
        assert run.harness_type == "shell"
        assert run.session_id == sess.session_id
        assert run.workspace_id == ws
        # Live pid must be set; for the real shell this is the actual
        # OS PID.  Allow 'starting' or 'running' — the background
        # thread updates the row asynchronously.
        assert run.status in ("starting", "running", "completed", "failed")
        assert run.runtime_process_id is not None

    def test_list_for_session_returns_newest_first(self, db):
        ws = _make_workspace(db)
        sess = _make_session(db, ws)
        svc = RunService(db)
        a = svc.start_for_session(
            session_id=sess.session_id,
            harness_type="shell",
            command="echo a",
        )
        b = svc.start_for_session(
            session_id=sess.session_id,
            harness_type="shell",
            command="echo b",
        )
        runs = svc.list_for_session(sess.session_id)
        # Newest first.
        assert runs[0].harness_run_id == b.harness_run_id
        assert runs[-1].harness_run_id == a.harness_run_id
