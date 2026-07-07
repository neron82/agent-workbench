"""Tests for the fork UX blueprint.

Covers:

* ``GET  /sessions/<session_id>/fork``  — form renders with the
  parent session type, a proposed new type, and empty form fields.
* ``POST /sessions/<session_id>/fork``  — happy path creates a
  :class:`ForkRecord` and a child :class:`SessionExtension` via
  :class:`ForkService`; validation errors are re-rendered in the
  form, and a redirect to the detail view is issued on success.
* ``GET  /forks/<fork_id>``  — detail view shows the parent/child
  linkage, the inherited summary, the structured payloads, and the
  versioned checkpoint.
* A 404 is returned when the parent session or the fork does not
  exist.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Tuple

import pytest

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.workspace import Workspace, WorkspaceRepository
from agent_workbench.web import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(db: sqlite3.Connection):
    """Return a Flask app that shares the test's DB connection.

    The factory's ``before_request`` handler would otherwise open a
    new connection per request, hiding the per-test DB state set up
    by other fixtures. Injecting the connection via
    ``WORKBENCH_DB_CONN`` keeps the test DB observable to both the
    app and the assertions.
    """
    flask_app = create_app(db_path=str(getattr(db, "_db_path", "/tmp/test_fork.db")))
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test"
    # Use the existing db connection for requests instead of opening new ones
    flask_app.config["WORKBENCH_DB_CONN"] = db
    # The teardown still tries to close connections it opened. Since
    # the per-request handler short-circuits when WORKBENCH_DB_CONN
    # is set, nothing is opened and the teardown is a no-op.
    yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def workspace(db: sqlite3.Connection) -> Workspace:
    repo = WorkspaceRepository(db)
    return repo.create(tenant_id="tenant-1", name="Test WS")


def _make_session(
    db: sqlite3.Connection,
    workspace_id: str,
    *,
    session_type: str = "chat",
) -> str:
    repo = SessionExtensionRepository(db)
    se = repo.create(workspace_id=workspace_id, session_type=session_type)
    return se.session_id


# ---------------------------------------------------------------------------
# GET /sessions/<session_id>/fork
# ---------------------------------------------------------------------------


class TestShowForkForm:
    def test_renders_form_for_existing_session(
        self, client, db: sqlite3.Connection, workspace: Workspace
    ) -> None:
        session_id = _make_session(db, workspace.workspace_id, session_type="chat")

        resp = client.get(f"/sessions/{session_id}/fork")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Fork session" in body
        assert session_id in body
        # Form fields present
        assert 'name="summary"' in body
        assert 'name="fork_reason"' in body
        assert 'name="decisions"' in body
        assert 'name="assumptions"' in body
        assert 'name="open_questions"' in body
        assert 'name="new_session_type"' in body
        # All three session types are offered
        for t in ("chat", "research", "work"):
            assert f'value="{t}"' in body or f">{t}<" in body

    def test_proposed_type_defaults_to_next_in_progression(
        self, client, db: sqlite3.Connection, workspace: Workspace
    ) -> None:
        # chat → research default
        sid = _make_session(db, workspace.workspace_id, session_type="chat")
        body = client.get(f"/sessions/{sid}/fork").get_data(as_text=True)
        # The 'selected' attribute should appear on the research option.
        assert 'value="research" selected' in body

        # research → work default
        sid2 = _make_session(db, workspace.workspace_id, session_type="research")
        body2 = client.get(f"/sessions/{sid2}/fork").get_data(as_text=True)
        assert 'value="work" selected' in body2

        # work stays at work
        sid3 = _make_session(db, workspace.workspace_id, session_type="work")
        body3 = client.get(f"/sessions/{sid3}/fork").get_data(as_text=True)
        assert 'value="work" selected' in body3

    def test_404_for_unknown_session(self, client) -> None:
        resp = client.get(f"/sessions/{uuid.uuid4().hex}/fork")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /sessions/<session_id>/fork
# ---------------------------------------------------------------------------


class TestCreateFork:
    def test_happy_path_creates_fork_and_redirects_to_detail(
        self, client, db: sqlite3.Connection, workspace: Workspace
    ) -> None:
        parent_id = _make_session(db, workspace.workspace_id, session_type="chat")

        resp = client.post(
            f"/sessions/{parent_id}/fork",
            data={
                "new_session_type": "research",
                "summary": "Initial research notes on transformer attention.",
                "fork_reason": "Promote chat into structured research.",
                "decisions": '{"approach": "transformer"}',
                "assumptions": '{"data_clean": true}',
                "open_questions": '{"q1": "What is the head count?"}',
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/forks/" in location

        # A fork record now exists in the DB.
        fork_id = location.rsplit("/", 1)[-1]
        row = db.execute(
            "SELECT parent_session_id, child_session_id, fork_kind, fork_reason, "
            "summary_ref, decisions_json, assumptions_json, open_questions_json "
            "FROM fork_records WHERE fork_id = ?",
            (fork_id,),
        ).fetchone()
        assert row is not None
        assert row["parent_session_id"] == parent_id
        assert row["fork_kind"] == "type_change"
        assert row["summary_ref"] == "Initial research notes on transformer attention."
        assert row["decisions_json"] == '{"approach": "transformer"}'

        # And a child session_extension exists with the new type and fork_id.
        child = db.execute(
            "SELECT session_type, fork_id FROM session_extensions "
            "WHERE session_id = ?",
            (row["child_session_id"],),
        ).fetchone()
        assert child is not None
        assert child["session_type"] == "research"
        assert child["fork_id"] == fork_id

    def test_empty_summary_rerenders_form_with_error(
        self, client, db: sqlite3.Connection, workspace: Workspace
    ) -> None:
        parent_id = _make_session(db, workspace.workspace_id, session_type="chat")

        resp = client.post(
            f"/sessions/{parent_id}/fork",
            data={"new_session_type": "work", "summary": "   "},
        )
        assert resp.status_code == 400
        body = resp.get_data(as_text=True)
        assert "summary is required" in body
        # Form re-rendered with the user's input preserved
        assert 'value="work" selected' in body

    def test_invalid_session_type_rerenders_form_with_error(
        self, client, db: sqlite3.Connection, workspace: Workspace
    ) -> None:
        parent_id = _make_session(db, workspace.workspace_id, session_type="chat")
        resp = client.post(
            f"/sessions/{parent_id}/fork",
            data={"new_session_type": "wat", "summary": "x"},
        )
        assert resp.status_code == 400
        assert "Invalid new_session_type" in resp.get_data(as_text=True)

    def test_invalid_json_in_optional_field_rerenders_form_with_error(
        self, client, db: sqlite3.Connection, workspace: Workspace
    ) -> None:
        parent_id = _make_session(db, workspace.workspace_id, session_type="chat")
        resp = client.post(
            f"/sessions/{parent_id}/fork",
            data={
                "new_session_type": "research",
                "summary": "valid",
                "decisions": "not-json",
            },
        )
        assert resp.status_code == 400
        body = resp.get_data(as_text=True)
        assert "decisions must be valid JSON" in body

    def test_non_object_json_rerenders_form_with_error(
        self, client, db: sqlite3.Connection, workspace: Workspace
    ) -> None:
        parent_id = _make_session(db, workspace.workspace_id, session_type="chat")
        resp = client.post(
            f"/sessions/{parent_id}/fork",
            data={
                "new_session_type": "research",
                "summary": "valid",
                "assumptions": "[1, 2, 3]",
            },
        )
        assert resp.status_code == 400
        body = resp.get_data(as_text=True)
        assert "JSON object" in body

    def test_404_for_unknown_parent(self, client) -> None:
        resp = client.post(
            f"/sessions/{uuid.uuid4().hex}/fork",
            data={"new_session_type": "research", "summary": "x"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /forks/<fork_id>
# ---------------------------------------------------------------------------


class TestShowForkDetail:
    def _create_fork(
        self,
        db: sqlite3.Connection,
        workspace_id: str,
        *,
        new_type: str = "research",
    ) -> Tuple[str, str, str]:
        parent = _make_session(db, workspace_id, session_type="chat")
        client = None  # type: ignore[assignment]
        # Use the service directly to keep the test independent of the
        # HTTP layer (already covered above).
        from agent_workbench.services.fork_service import ForkService

        child_id = uuid.uuid4().hex
        fork = ForkService(db).create_fork(
            parent_session_id=parent,
            child_session_id=child_id,
            new_session_type=new_type,
            fork_reason="detail test",
            initiated_by="user",
            summary="Summary for the detail test.",
            decisions={"key": "value"},
            assumptions={"assumption": 1},
            open_questions={"q": "why"},
            relevant_artifacts={"art": "a-1"},
        )
        return fork.fork_id, parent, child_id

    def test_renders_parent_child_linkage_and_context(
        self, client, db: sqlite3.Connection, workspace: Workspace
    ) -> None:
        fork_id, parent_id, child_id = self._create_fork(db, workspace.workspace_id)

        resp = client.get(f"/forks/{fork_id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        # Parent + child linkage
        assert parent_id in body
        assert child_id in body

        # Summary
        assert "Summary for the detail test." in body

        # Structured payloads surface as JSON
        assert '"key": "value"' in body or '"key":"value"' in body
        assert "checkpoint" in body.lower()

        # Fork metadata
        assert "type_change" in body or "branch" in body
        assert "user" in body  # initiated_by

    def test_404_for_unknown_fork(self, client) -> None:
        resp = client.get(f"/forks/{uuid.uuid4().hex}")
        assert resp.status_code == 404
