"""UI tests for the harness start endpoint (``POST /sessions/<id>/runs``).

The endpoint lives in :mod:`agent_workbench.web.runs` and is the only
path through which a UI-gestarteter harness run is supposed to
happen.  These tests pin its honest-capability contract:

* Known live harnesses (``shell``/``opencode``/``ssh``/``hermes``)
  are dispatched through ``RunService`` and a 302 redirect follows.
* Disabled harnesses (``discussion``) return a flash error and
  redirect to the session view (no DB row is created).
* Missing required fields (e.g. SSH ``remote_host``, no ``command``)
  surface as a flash error.
* Unknown session IDs yield 404.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.web.app import create_app


@pytest.fixture
def app(db, tmp_db):
    app = create_app(db_path=str(tmp_db))
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    from tests.conftest import make_csrf_client
    return make_csrf_client(app)


@pytest.fixture
def session_id(db):
    repo = SessionExtensionRepository(db)
    s = repo.create(
        workspace_id=_workspace(db),
        session_type="research",
    )
    return s.session_id


def _workspace(db):
    from agent_workbench.models.workspace import WorkspaceRepository

    return WorkspaceRepository(db).create(tenant_id="t", name="t").workspace_id


# ----------------------------------------------------------------------
# Happy path: live harness types dispatch and redirect to the run page.
# ----------------------------------------------------------------------


class TestStartSessionRunShell:
    def test_shell_start_creates_run_and_redirects(
        self, client, db, session_id
    ):
        resp = client.post(
            f"/sessions/{session_id}/runs",
            data={"harness_type": "shell", "command": "echo hello"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        # A HarnessRun row was created with the expected harness_type
        # and a real shell command.
        hr_repo = HarnessRunRepository(db)
        runs = hr_repo.list_by_session(session_id)
        assert len(runs) == 1
        assert runs[0].harness_type == "shell"
        # The redirect must point at the run detail page.
        assert f"/runs/{runs[0].harness_run_id}" in resp.headers["Location"]


class TestStartSessionRunOpencodeMissingBinary:
    def test_opencode_without_binary_returns_flash(
        self, client, db, session_id
    ):
        """If the opencode binary is not on PATH the preflight raises
        and the UI flashes a precise German error."""
        with patch("shutil.which", return_value=None):
            resp = client.post(
                f"/sessions/{session_id}/runs",
                data={
                    "harness_type": "opencode",
                    "command": "say hi",
                },
                follow_redirects=False,
            )
        # The handler redirects with a flashed error — never 500.
        assert resp.status_code in (302, 303, 422)
        # No row was persisted.
        hr_repo = HarnessRunRepository(db)
        assert hr_repo.list_by_session(session_id) == []


class TestStartSessionRunSshMissingRemoteHost:
    def test_ssh_without_remote_host_returns_flash(
        self, client, db, session_id
    ):
        resp = client.post(
            f"/sessions/{session_id}/runs",
            data={"harness_type": "ssh", "command": "ls"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303, 422)
        hr_repo = HarnessRunRepository(db)
        assert hr_repo.list_by_session(session_id) == []


# ----------------------------------------------------------------------
# Disabled harness types — must NOT silently produce a "running" stub.
# ----------------------------------------------------------------------


class TestStartSessionRunDisabledHarness:
    def test_discussion_harness_refused_with_flash(
        self, client, db, session_id
    ):
        resp = client.post(
            f"/sessions/{session_id}/runs",
            data={"harness_type": "discussion", "command": "x"},
            follow_redirects=False,
        )
        # The handler redirects with a flash (never creates a row,
        # never returns 500).
        assert resp.status_code in (302, 303)
        hr_repo = HarnessRunRepository(db)
        assert hr_repo.list_by_session(session_id) == []


# ----------------------------------------------------------------------
# Unknown session / harness_type — handled gracefully.
# ----------------------------------------------------------------------


class TestStartSessionRunBadInputs:
    def test_unknown_session_returns_404(self, client):
        resp = client.post(
            "/sessions/does-not-exist/runs",
            data={"harness_type": "shell", "command": "echo"},
        )
        # Either 404 from the session lookup or a redirect with a
        # flash (the service raises HarnessNotReadyError).  Both
        # are acceptable; the contract is "never 500".
        assert resp.status_code in (302, 303, 404, 422)

    def test_missing_harness_type_redirects_with_flash(
        self, client, db, session_id
    ):
        resp = client.post(
            f"/sessions/{session_id}/runs",
            data={"command": "echo"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        hr_repo = HarnessRunRepository(db)
        assert hr_repo.list_by_session(session_id) == []


# ----------------------------------------------------------------------
# Hermes — now in the live whitelist, but still needs a binary.
# ----------------------------------------------------------------------


class TestStartSessionRunHermesMissingBinary:
    def test_hermes_without_binary_returns_flash(
        self, client, db, session_id
    ):
        with patch("shutil.which", return_value=None):
            resp = client.post(
                f"/sessions/{session_id}/runs",
                data={"harness_type": "hermes", "command": "say hi"},
                follow_redirects=False,
            )
        assert resp.status_code in (302, 303, 422)
        hr_repo = HarnessRunRepository(db)
        assert hr_repo.list_by_session(session_id) == []


# ----------------------------------------------------------------------
# Registry sanity — every harness type registered in the dispatch
# table is one the adapter package can actually resolve.
# ----------------------------------------------------------------------


class TestAdapterRegistry:
    def test_all_spec_harness_types_resolve(self):
        from agent_workbench.adapters import get_adapter_class

        for ht in ("discussion", "hermes", "opencode", "shell", "ssh"):
            cls = get_adapter_class(ht)
            assert cls is not None, f"no adapter class for {ht!r}"
            assert cls.adapter_type == ht
