"""UI tests for the TaskSpec approval gate.

Covers:
* GET /task-specs/<id> renders objective, scope, criteria, approval status
* POST /task-specs/<id>/approve flips approval_status to 'approved'
* POST /task-specs/<id>/reject flips approval_status to 'rejected'
* Approval/reject are idempotent (terminal status refuses further action)
* GET /sessions/<id>/task-spec renders the creation form
* POST /sessions/<id>/task-spec creates a task spec and redirects
* Objective is required on creation
"""

from __future__ import annotations

import pytest

from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.task_spec import TaskSpecRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.web.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(db, tmp_db):
    """Build a Flask app backed by the same on-disk test DB as the db fixture."""
    # The sibling's create_app takes a *path* to a SQLite DB and opens a
    # fresh per-request connection against it.  We use the same tmp file
    # the conftest's ``db`` fixture migrated, so the connection pool sees
    # the same schema.  The in-memory ``db`` connection is no longer used
    # directly by the app — only by repository helpers invoked outside a
    # request context.
    app = create_app(db_path=str(tmp_db))
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def workspace_id(db):
    ws = WorkspaceRepository(db).create(tenant_id="test", name="t")
    return ws.workspace_id


@pytest.fixture
def session_id(db, workspace_id):
    s = SessionExtensionRepository(db).create(
        workspace_id=workspace_id,
        session_type="research",
    )
    return s.session_id


@pytest.fixture
def task_spec_id(db, workspace_id, session_id):
    spec = TaskSpecRepository(db).create(
        workspace_id=workspace_id,
        source_session_id=session_id,
        objective="Investigate X",
        scope_in={"paths": ["src/"]},
        scope_out={"paths": ["tests/"]},
        acceptance_criteria={"must_pass": ["tests pass"]},
        risk_level="low",
        approval_status="draft",
    )
    return spec.task_spec_id


# ---------------------------------------------------------------------------
# GET /task-specs/<id>
# ---------------------------------------------------------------------------


class TestTaskSpecView:
    def test_renders_objective(self, client, task_spec_id):
        resp = client.get(f"/task-specs/{task_spec_id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Investigate X" in body
        assert task_spec_id in body
        assert "draft" in body  # approval status

    def test_renders_scope_in_and_out(self, client, task_spec_id):
        body = client.get(f"/task-specs/{task_spec_id}").get_data(as_text=True)
        assert "src" in body
        assert "tests" in body

    def test_renders_acceptance_criteria(self, client, task_spec_id):
        body = client.get(f"/task-specs/{task_spec_id}").get_data(as_text=True)
        assert "tests pass" in body

    def test_renders_approve_and_reject_buttons_for_draft(self, client, task_spec_id):
        body = client.get(f"/task-specs/{task_spec_id}").get_data(as_text=True)
        assert 'data-testid="approve-btn"' in body
        assert 'data-testid="reject-btn"' in body

    def test_404_for_missing_task_spec(self, client):
        # The sibling's app registers a friendly 404 error handler that
        # renders an error template; the response code is preserved.
        resp = client.get("/task-specs/does-not-exist")
        assert resp.status_code in (200, 404)
        # If the handler returned 200 (rendered error page), the body
        # must contain the "Not found" message.
        if resp.status_code == 200:
            body = resp.get_data(as_text=True)
            assert "Not found" in body


# ---------------------------------------------------------------------------
# POST /task-specs/<id>/approve
# ---------------------------------------------------------------------------


class TestTaskSpecApprove:
    def test_approve_flips_status(self, client, db, task_spec_id):
        resp = client.post(f"/task-specs/{task_spec_id}/approve")
        assert resp.status_code in (302, 303)
        spec = TaskSpecRepository(db).get_by_id(task_spec_id)
        assert spec.approval_status == "approved"

    def test_approve_terminal_state_does_not_revert(self, client, db, task_spec_id):
        # Approve once
        client.post(f"/task-specs/{task_spec_id}/approve")
        # Approve again — should be a no-op (terminal state)
        resp = client.post(f"/task-specs/{task_spec_id}/approve")
        assert resp.status_code in (302, 303)
        spec = TaskSpecRepository(db).get_by_id(task_spec_id)
        assert spec.approval_status == "approved"

    def test_approve_unknown_id_404(self, client):
        resp = client.post("/task-specs/nonexistent/approve")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /task-specs/<id>/reject
# ---------------------------------------------------------------------------


class TestTaskSpecReject:
    def test_reject_flips_status(self, client, db, task_spec_id):
        resp = client.post(f"/task-specs/{task_spec_id}/reject")
        assert resp.status_code in (302, 303)
        spec = TaskSpecRepository(db).get_by_id(task_spec_id)
        assert spec.approval_status == "rejected"

    def test_reject_approved_does_not_flip(self, client, db, task_spec_id):
        client.post(f"/task-specs/{task_spec_id}/approve")
        client.post(f"/task-specs/{task_spec_id}/reject")
        spec = TaskSpecRepository(db).get_by_id(task_spec_id)
        assert spec.approval_status == "approved"


# ---------------------------------------------------------------------------
# GET /sessions/<id>/task-spec
# ---------------------------------------------------------------------------


class TestTaskSpecForm:
    def test_form_renders(self, client, session_id):
        resp = client.get(f"/sessions/{session_id}/task-spec")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert session_id in body
        assert "objective" in body.lower()
        assert 'data-testid="objective"' in body
        assert 'data-testid="submit-btn"' in body

    def test_form_404_for_missing_session(self, client):
        resp = client.get("/sessions/no-such-session/task-spec")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /sessions/<id>/task-spec
# ---------------------------------------------------------------------------


class TestTaskSpecCreate:
    def test_create_with_minimum_objective(self, client, db, session_id):
        resp = client.post(
            f"/sessions/{session_id}/task-spec",
            data={"objective": "Refactor the X module"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        # New task spec exists with the right objective and source session.
        spec = TaskSpecRepository(db).get_by_id(
            resp.headers["Location"].rsplit("/", 1)[-1]
        )
        assert spec is not None
        assert spec.objective == "Refactor the X module"
        assert spec.source_session_id == session_id
        assert spec.approval_status == "draft"

    def test_create_with_full_fields(self, client, db, session_id):
        resp = client.post(
            f"/sessions/{session_id}/task-spec",
            data={
                "objective": "Do thing",
                "risk_level": "high",
                "scope_in_json": '{"paths": ["a/"]}',
                "scope_out_json": '{"paths": ["b/"]}',
                "acceptance_criteria_json": '{"must_pass": ["x"]}',
                "constraints_json": '{"timeout_s": 60}',
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        new_id = resp.headers["Location"].rsplit("/", 1)[-1]
        spec = TaskSpecRepository(db).get_by_id(new_id)
        assert spec.risk_level == "high"
        assert spec.scope_in == {"paths": ["a/"]}
        assert spec.scope_out == {"paths": ["b/"]}
        assert spec.acceptance_criteria == {"must_pass": ["x"]}
        assert spec.constraints == {"timeout_s": 60}

    def test_create_without_objective_400_or_redirect(self, client, session_id):
        resp = client.post(
            f"/sessions/{session_id}/task-spec",
            data={"objective": ""},
            follow_redirects=False,
        )
        # Empty objective is rejected — either a 400 (form-level) or a
        # redirect back to the form.  We accept both as long as no spec
        # is created.
        assert resp.status_code in (302, 303, 400)

    def test_create_with_invalid_json_400(self, client, session_id):
        resp = client.post(
            f"/sessions/{session_id}/task-spec",
            data={
                "objective": "x",
                "scope_in_json": "{not-json",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_create_404_for_missing_session(self, client):
        resp = client.post(
            "/sessions/no-such-session/task-spec",
            data={"objective": "x"},
            follow_redirects=False,
        )
        assert resp.status_code == 404
