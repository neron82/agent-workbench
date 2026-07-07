"""Tests for DiscussionAdapter."""

import pytest

from agent_workbench.adapters.discussion import DiscussionAdapter
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


@pytest.fixture
def adapter(db):
    return DiscussionAdapter(db)


# ------------------------------------------------------------------
# Capabilities
# ------------------------------------------------------------------


class TestDiscussionAdapterCapabilities:
    def test_all_capabilities_false(self, adapter):
        caps = adapter.capabilities
        assert caps.can_stop is False
        assert caps.can_cancel is False
        assert caps.can_pause is False
        assert caps.can_steer is False
        assert caps.can_shell is False
        assert caps.can_file_write is False
        assert caps.can_diff is False
        assert caps.can_remote is False
        assert caps.can_replay is False
        assert caps.has_process_ids is False

    def test_adapter_type_is_discussion(self, adapter):
        assert adapter.adapter_type == "discussion"

    def test_capabilities_dict_all_false(self, adapter):
        d = adapter.capabilities_dict()
        assert all(v is False for v in d.values())


# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------


class TestDiscussionAdapterStart:
    def test_start_creates_harness_run(self, adapter, repo, workspace):
        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="discuss something",
        )
        hr = repo.get_by_id(hr_id)
        assert hr is not None
        assert isinstance(hr, HarnessRun)
        assert hr.harness_type == "discussion"
        assert hr.status == "running"
        assert hr.workspace_id == workspace.workspace_id
        assert hr.session_id == "session-1"

    def test_start_returns_harness_run_id(self, adapter, workspace):
        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="discuss",
        )
        assert hr_id is not None
        assert len(hr_id) > 0


class TestDiscussionAdapterStop:
    def test_stop_updates_status_to_completed(self, adapter, repo, workspace):
        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="discuss",
        )
        adapter.stop(hr_id)
        hr = repo.get_by_id(hr_id)
        assert hr.status == "completed"
        assert hr.ended_at is not None

    def test_stop_nonexistent_raises(self, adapter):
        with pytest.raises(Exception):
            adapter.stop("nonexistent-id")


class TestDiscussionAdapterCancel:
    def test_cancel_updates_status_to_cancelled(self, adapter, repo, workspace):
        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="discuss",
        )
        adapter.cancel(hr_id)
        hr = repo.get_by_id(hr_id)
        assert hr.status == "cancelled"
        assert hr.ended_at is not None

    def test_cancel_nonexistent_raises(self, adapter):
        with pytest.raises(Exception):
            adapter.cancel("nonexistent-id")


# ------------------------------------------------------------------
# Runtime info
# ------------------------------------------------------------------


class TestDiscussionAdapterRuntimeIds:
    def test_get_runtime_ids_has_session_id(self, adapter, workspace):
        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="discuss",
        )
        ids = adapter.get_runtime_ids(hr_id)
        assert ids.session_id == "session-1"
        assert ids.process_id is None
        assert ids.remote_host is None
        assert ids.remote_pid is None


class TestDiscussionAdapterTranscript:
    def test_get_transcript_is_empty(self, adapter, workspace):
        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="discuss",
        )
        transcript = adapter.get_transcript(hr_id)
        assert transcript.stdout == ""
        assert transcript.stderr == ""


# ------------------------------------------------------------------
# Side-effect rejection
# ------------------------------------------------------------------


class TestDiscussionAdapterRejectSideEffects:
    def test_reject_shell(self, adapter, workspace):
        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="discuss",
        )
        with pytest.raises(NotImplementedError):
            adapter.execute_shell(hr_id, "ls")

    def test_reject_file_write(self, adapter, workspace):
        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="discuss",
        )
        with pytest.raises(NotImplementedError):
            adapter.write_file(hr_id, "/tmp/test.txt", "data")

    def test_reject_replay(self, adapter, workspace):
        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="discuss",
        )
        with pytest.raises(NotImplementedError):
            adapter.replay(hr_id)

    def test_reject_steer(self, adapter, workspace):
        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="discuss",
        )
        with pytest.raises(NotImplementedError):
            adapter.steer(hr_id, "do something")
