"""Tests for HarnessRunRepository."""

import pytest

from agent_workbench.models.harness_run import HarnessRun, HarnessRunRepository
from agent_workbench.models.workspace import WorkspaceRepository


@pytest.fixture
def repo(db):
    return HarnessRunRepository(db)


@pytest.fixture
def ws_repo(db):
    return WorkspaceRepository(db)


@pytest.fixture
def workspace(ws_repo):
    return ws_repo.create(tenant_id="tenant-1", name="Test Workspace")


# ------------------------------------------------------------------
# Create
# ------------------------------------------------------------------


class TestHarnessRunCreate:
    def test_create_returns_dataclass(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="discussion",
        )
        assert isinstance(hr, HarnessRun)
        assert hr.workspace_id == workspace.workspace_id
        assert hr.session_id == "session-1"
        assert hr.harness_type == "discussion"
        assert hr.status == "queued"
        assert hr.harness_run_id is not None

    def test_create_generates_unique_ids(self, repo, workspace):
        hr1 = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="discussion",
        )
        hr2 = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="hermes",
        )
        assert hr1.harness_run_id != hr2.harness_run_id

    def test_create_with_task_spec(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="opencode",
            task_spec_id=None,
        )
        assert hr.task_spec_id is None

    def test_create_with_control_capabilities(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="hermes",
            control_capabilities={"can_stop": True, "can_resize": False},
        )
        assert hr.control_capabilities_json is not None

    def test_create_with_custom_status(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="shell",
            status="running",
        )
        assert hr.status == "running"


# ------------------------------------------------------------------
# Get by ID
# ------------------------------------------------------------------


class TestHarnessRunGetById:
    def test_get_existing(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="discussion",
        )
        found = repo.get_by_id(hr.harness_run_id)
        assert found is not None
        assert found.harness_run_id == hr.harness_run_id
        assert found.harness_type == "discussion"

    def test_get_nonexistent(self, repo):
        assert repo.get_by_id("nonexistent") is None


# ------------------------------------------------------------------
# List by session
# ------------------------------------------------------------------


class TestHarnessRunListBySession:
    def test_list_empty(self, repo, workspace):
        assert repo.list_by_session("session-1") == []

    def test_list_multiple(self, repo, workspace):
        repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="discussion",
        )
        repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="hermes",
        )
        runs = repo.list_by_session("session-1")
        assert len(runs) == 2
        assert all(isinstance(r, HarnessRun) for r in runs)

    def test_list_isolated_to_session(self, repo, workspace):
        repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="discussion",
        )
        repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-2",
            harness_type="hermes",
        )
        assert len(repo.list_by_session("session-1")) == 1
        assert len(repo.list_by_session("session-2")) == 1


# ------------------------------------------------------------------
# List by workspace
# ------------------------------------------------------------------


class TestHarnessRunListByWorkspace:
    def test_list_empty(self, repo, workspace):
        assert repo.list_by_workspace(workspace.workspace_id) == []

    def test_list_multiple(self, repo, workspace):
        repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="discussion",
        )
        repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-2",
            harness_type="hermes",
        )
        runs = repo.list_by_workspace(workspace.workspace_id)
        assert len(runs) == 2


# ------------------------------------------------------------------
# Update status
# ------------------------------------------------------------------


class TestHarnessRunUpdateStatus:
    def test_update_status(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="discussion",
        )
        updated = repo.update_status(
            hr.harness_run_id, status="running", started_at=1000.0
        )
        assert updated is not None
        assert updated.status == "running"
        assert updated.started_at == 1000.0

    def test_update_status_nonexistent(self, repo):
        assert repo.update_status("nonexistent", status="running") is None

    def test_update_status_with_ended_at(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="discussion",
        )
        updated = repo.update_status(
            hr.harness_run_id, status="completed", ended_at=2000.0
        )
        assert updated.ended_at == 2000.0
        assert updated.status == "completed"


# ------------------------------------------------------------------
# Update runtime IDs
# ------------------------------------------------------------------


class TestHarnessRunUpdateRuntimeIds:
    def test_update_runtime_ids(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="hermes",
        )
        updated = repo.update_runtime_ids(
            hr.harness_run_id,
            runtime_session_id="runtime-session-1",
            runtime_process_id="pid-42",
            runtime_remote_process_id="remote-pid-99",
        )
        assert updated is not None
        assert updated.runtime_session_id == "runtime-session-1"
        assert updated.runtime_process_id == "pid-42"
        assert updated.runtime_remote_process_id == "remote-pid-99"

    def test_update_runtime_ids_partial(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="hermes",
        )
        updated = repo.update_runtime_ids(
            hr.harness_run_id, runtime_process_id="pid-42"
        )
        assert updated.runtime_process_id == "pid-42"

    def test_update_runtime_ids_no_changes(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="hermes",
        )
        result = repo.update_runtime_ids(hr.harness_run_id)
        assert result is not None
        assert result.harness_run_id == hr.harness_run_id


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------


class TestHarnessRunDelete:
    def test_delete_existing(self, repo, workspace):
        hr = repo.create(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            harness_type="discussion",
        )
        assert repo.delete(hr.harness_run_id) is True
        assert repo.get_by_id(hr.harness_run_id) is None

    def test_delete_nonexistent(self, repo):
        assert repo.delete("nonexistent") is False
