"""Tests for run-detail persistence after server restart."""

from __future__ import annotations

import subprocess
import time

import pytest

from agent_workbench.adapters.shell import ShellAdapter
from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.services import TranscriptService
from agent_workbench.web.app import create_app


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "persist.db"


@pytest.fixture
def app(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_DB_PATH", str(db_path))
    monkeypatch.setenv("WORKBENCH_SECRETS_FILE", str(tmp_path / ".secrets"))
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


def test_transcript_survives_connection_close(app, db_path, tmp_path, monkeypatch):
    """The transcript must still be loadable after the original connection
    is closed (simulating a server restart)."""
    # 1. Start a run, write some transcript, close the connection.
    c1 = get_connection(str(db_path))
    apply_migrations(c1)
    ws = WorkspaceRepository(c1).create(tenant_id="t1", name="t")
    c1.commit()
    adapter = ShellAdapter(c1)
    rid = adapter.start(
        workspace_id=ws.workspace_id, session_id="s1",
        command="printf 'persistent-line-1\\npersistent-line-2\\n'",
    )
    # Wait for the collector to finish.
    for _ in range(50):
        time.sleep(0.05)
        run = HarnessRunRepository(c1).get_by_id(rid)
        if run.status in ("completed", "failed", "cancelled"):
            break
    c1.close()

    # 2. Reopen the database from a fresh Flask app context (simulates a restart).
    with app.app_context():
        from agent_workbench.web.runs import _load_transcript
        c2 = get_connection(str(db_path))
        run = HarnessRunRepository(c2).get_by_id(rid)
        assert run is not None
        assert run.status == "completed"
        # _load_transcript uses the request-scoped get_db(), so we call
        # the service directly here.
        rows = TranscriptService().list(c2, harness_run_id=rid)
        assert any("persistent-line-1" in r["content"] for r in rows)
        assert any("persistent-line-2" in r["content"] for r in rows)


def test_lifecycle_events_persisted(db_path):
    """Lifecycle events written by adapters must be queryable after restart."""
    c1 = get_connection(str(db_path))
    apply_migrations(c1)
    ws = WorkspaceRepository(c1).create(tenant_id="t1", name="t")
    c1.commit()
    adapter = ShellAdapter(c1)
    rid = adapter.start(
        workspace_id=ws.workspace_id, session_id="s1", command="true"
    )
    for _ in range(50):
        time.sleep(0.05)
        run = HarnessRunRepository(c1).get_by_id(rid)
        if run.status in ("completed", "failed"):
            break
    c1.close()

    c2 = get_connection(str(db_path))
    events = TranscriptService().list_events(c2, harness_run_id=rid)
    types = [e["event_type"] for e in events]
    assert "start" in types
    assert "exit" in types


def test_exit_code_persisted(db_path):
    """exit_code must be written by the collector and survive a restart."""
    c1 = get_connection(str(db_path))
    apply_migrations(c1)
    ws = WorkspaceRepository(c1).create(tenant_id="t1", name="t")
    c1.commit()
    adapter = ShellAdapter(c1)
    rid = adapter.start(
        workspace_id=ws.workspace_id, session_id="s1", command="exit 7"
    )
    for _ in range(50):
        time.sleep(0.05)
        run = HarnessRunRepository(c1).get_by_id(rid)
        if run.status in ("completed", "failed"):
            break
    c1.close()

    c2 = get_connection(str(db_path))
    run = HarnessRunRepository(c2).get_by_id(rid)
    assert run.status == "failed"
    assert run.exit_code == 7
