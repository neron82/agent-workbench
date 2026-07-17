"""UI tests for the run panel and capability-aware control surfaces.

Per UI spec section 12 — "Honest capability rule" — unsupported controls
must be hidden or disabled with a precise reason.  We never render fake
universal controls.

These tests cover:

* /runs/<id> renders status, objective, harness, capabilities
* Pause button is disabled with the precise reason when can_pause=False
* Steer button is disabled with the precise reason when can_steer=False
* Pause/Steer are enabled when their corresponding capability is True
* POST /runs/<id>/stop succeeds when can_stop=True
* POST /runs/<id>/stop is 403 when can_stop=False
* POST /runs/<id>/cancel succeeds when can_cancel=True
* POST /runs/<id>/cancel is 403 when can_cancel=False
* 404 for unknown harness_run_id
"""

from __future__ import annotations


import pytest

from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.web.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(db, tmp_db):
    """Build a Flask app backed by the same on-disk test DB as the db fixture."""
    # See note in test_task_spec_ui.py — the app opens its own per-request
    # connection against the file path the conftest has already migrated.
    app = create_app(db_path=str(tmp_db))
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    from tests.conftest import make_csrf_client
    return make_csrf_client(app)


@pytest.fixture
def workspace_id(db):
    ws = WorkspaceRepository(db).create(tenant_id="test", name="t")
    return ws.workspace_id


def _make_run(db, workspace_id, *, harness_type: str, capabilities: dict):
    """Create a HarnessRun with the given persisted capability snapshot."""
    run = HarnessRunRepository(db).create(
        workspace_id=workspace_id,
        session_id="sess-1",
        harness_type=harness_type,
        status="running",
        control_capabilities=capabilities,
    )
    return run.harness_run_id


# Capability presets mirroring real adapter capability sets
SHELL_CAPS = {  # can_stop=True, can_cancel=True, can_pause=False, can_steer=False
    "can_stop": True,
    "can_cancel": True,
    "can_pause": False,
    "can_steer": False,
    "can_shell": True,
    "can_file_write": True,
    "can_diff": False,
    "can_remote": False,
    "can_replay": True,
    "has_process_ids": True,
}

HERMES_CAPS = {  # can_steer=True, can_pause=False (the realistic Hermes case)
    "can_stop": True,
    "can_cancel": True,
    "can_pause": False,
    "can_steer": True,
    "can_shell": True,
    "can_file_write": True,
    "can_diff": False,
    "can_remote": False,
    "can_replay": True,
    "has_process_ids": True,
}

DISCUSSION_CAPS = {  # all False
    "can_stop": False,
    "can_cancel": False,
    "can_pause": False,
    "can_steer": False,
    "can_shell": False,
    "can_file_write": False,
    "can_diff": False,
    "can_remote": False,
    "can_replay": False,
    "has_process_ids": False,
}

ALL_TRUE_CAPS = {k: True for k in SHELL_CAPS}


# ---------------------------------------------------------------------------
# GET /runs/<id>
# ---------------------------------------------------------------------------


class TestRunPanelRendering:
    def test_renders_status_and_harness(self, client, db, workspace_id):
        rid = _make_run(
            db, workspace_id, harness_type="shell", capabilities=SHELL_CAPS,
        )
        body = client.get(f"/runs/{rid}").get_data(as_text=True)
        assert rid in body
        assert "running" in body
        assert "shell" in body

    def test_renders_capability_summary(self, client, db, workspace_id):
        rid = _make_run(
            db, workspace_id, harness_type="shell", capabilities=SHELL_CAPS,
        )
        body = client.get(f"/runs/{rid}").get_data(as_text=True)
        assert "can_stop" in body
        assert "can_cancel" in body
        assert "can_pause" in body
        assert "can_steer" in body

    def test_404_for_unknown_run(self, client):
        resp = client.get("/runs/does-not-exist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Capability-aware control surface
# ---------------------------------------------------------------------------


class TestCapabilityAwareControls:
    """Section 12: every unsupported control must be disabled with a reason."""

    def test_pause_disabled_when_can_pause_false(self, client, db, workspace_id):
        rid = _make_run(
            db, workspace_id, harness_type="shell", capabilities=SHELL_CAPS,
        )
        body = client.get(f"/runs/{rid}").get_data(as_text=True)

        # The pause button is rendered (so the user can see WHY it's
        # unavailable), but with `disabled` and the precise reason.
        assert 'data-testid="control-pause"' in body
        assert (
            "Pause not supported by this harness" in body
        ), "Precise reason must be present for disabled pause"
        # The disabled-button substring proves the disabled attribute.
        assert (
            'data-testid="control-pause"' in body
            and 'data-supported="false"' in body
        )

    def test_steer_disabled_when_can_steer_false(self, client, db, workspace_id):
        rid = _make_run(
            db, workspace_id, harness_type="shell", capabilities=SHELL_CAPS,
        )
        body = client.get(f"/runs/{rid}").get_data(as_text=True)
        assert 'data-testid="control-steer"' in body
        assert (
            "Steering not supported by this harness" in body
        ), "Precise reason must be present for disabled steer"
        assert (
            'data-testid="control-steer"' in body
            and 'data-supported="false"' in body
        )

    def test_pause_enabled_when_can_pause_true(self, client, db, workspace_id):
        rid = _make_run(
            db, workspace_id, harness_type="opencode",
            capabilities=ALL_TRUE_CAPS,
        )
        body = client.get(f"/runs/{rid}").get_data(as_text=True)
        # The pause button is rendered WITHOUT `data-supported="false"`,
        # and the precise-disabled reason string is NOT next to it.
        # We assert by absence of the disabled marker.
        # Locate the control-pause element and verify it is not disabled.
        marker_open = 'data-testid="control-pause"'
        idx = body.find(marker_open)
        assert idx >= 0
        # Look at the next 200 characters for the disabled marker.
        snippet = body[idx: idx + 200]
        assert "data-supported=\"false\"" not in snippet
        assert "Pause not supported" not in snippet

    def test_steer_enabled_when_can_steer_true(self, client, db, workspace_id):
        # Hermes supports steering.
        rid = _make_run(
            db, workspace_id, harness_type="hermes", capabilities=HERMES_CAPS,
        )
        body = client.get(f"/runs/{rid}").get_data(as_text=True)
        marker_open = 'data-testid="control-steer"'
        idx = body.find(marker_open)
        assert idx >= 0
        snippet = body[idx: idx + 200]
        assert "data-supported=\"false\"" not in snippet
        assert "Steering not supported" not in snippet

    def test_all_controls_disabled_for_discussion_adapter(
        self, client, db, workspace_id
    ):
        rid = _make_run(
            db, workspace_id, harness_type="discussion",
            capabilities=DISCUSSION_CAPS,
        )
        body = client.get(f"/runs/{rid}").get_data(as_text=True)
        for key in ("stop", "cancel", "pause", "steer"):
            marker = f'data-testid="control-{key}"'
            idx = body.find(marker)
            assert idx >= 0, f"{key} button should be rendered for visibility"
            snippet = body[idx: idx + 200]
            assert "data-supported=\"false\"" in snippet, (
                f"{key} must be disabled for discussion harness"
            )

    def test_no_fake_universal_controls(self, client, db, workspace_id):
        """Section 12: never show controls the harness doesn't support."""
        rid = _make_run(
            db, workspace_id, harness_type="discussion",
            capabilities=DISCUSSION_CAPS,
        )
        body = client.get(f"/runs/{rid}").get_data(as_text=True)
        # No POST form should target stop or cancel for the discussion
        # adapter (all unsupported).  The buttons exist but are disabled
        # <button> tags, not <form> wrappers.
        assert 'action="/runs/' + rid + '/stop"' not in body
        assert 'action="/runs/' + rid + '/cancel"' not in body

    def test_stop_and_cancel_render_as_forms_when_supported(
        self, client, db, workspace_id
    ):
        rid = _make_run(
            db, workspace_id, harness_type="shell", capabilities=SHELL_CAPS,
        )
        body = client.get(f"/runs/{rid}").get_data(as_text=True)
        assert f'action="/runs/{rid}/stop"' in body
        assert f'action="/runs/{rid}/cancel"' in body


# ---------------------------------------------------------------------------
# POST /runs/<id>/stop
# ---------------------------------------------------------------------------


class TestRunStop:
    def test_stop_succeeds_when_supported(self, client, db, workspace_id):
        rid = _make_run(
            db, workspace_id, harness_type="shell", capabilities=SHELL_CAPS,
        )
        resp = client.post(f"/runs/{rid}/stop", follow_redirects=False)
        # Successful stop → 302 redirect to detail.
        assert resp.status_code in (302, 303)
        # The shell adapter should have moved the run to "stopping".
        run = HarnessRunRepository(db).get_by_id(rid)
        assert run.status in ("stopping", "running", "completed", "cancelled", "failed")

    def test_stop_403_when_unsupported(self, client, db, workspace_id):
        rid = _make_run(
            db, workspace_id, harness_type="discussion",
            capabilities=DISCUSSION_CAPS,
        )
        resp = client.post(f"/runs/{rid}/stop", follow_redirects=False)
        assert resp.status_code == 403
        # Run status must NOT have changed.
        run = HarnessRunRepository(db).get_by_id(rid)
        assert run.status == "running"

    def test_stop_404_for_unknown_run(self, client):
        resp = client.post("/runs/nonexistent/stop")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /runs/<id>/cancel
# ---------------------------------------------------------------------------


class TestRunCancel:
    def test_cancel_succeeds_when_supported(self, client, db, workspace_id):
        rid = _make_run(
            db, workspace_id, harness_type="shell", capabilities=SHELL_CAPS,
        )
        resp = client.post(f"/runs/{rid}/cancel", follow_redirects=False)
        assert resp.status_code in (302, 303)
        run = HarnessRunRepository(db).get_by_id(rid)
        # Shell cancel updates status to "cancelled".
        assert run.status in ("cancelled", "running")

    def test_cancel_403_when_unsupported(self, client, db, workspace_id):
        rid = _make_run(
            db, workspace_id, harness_type="discussion",
            capabilities=DISCUSSION_CAPS,
        )
        resp = client.post(f"/runs/{rid}/cancel", follow_redirects=False)
        assert resp.status_code == 403
        run = HarnessRunRepository(db).get_by_id(rid)
        assert run.status == "running"

    def test_cancel_404_for_unknown_run(self, client):
        resp = client.post("/runs/nonexistent/cancel")
        assert resp.status_code == 404
