"""Tests for OpencodeAdapter."""

from unittest.mock import MagicMock, patch

import pytest

from agent_workbench.adapters.opencode import OpencodeAdapter
from agent_workbench.adapters.base import (
    HarnessNotReadyError,
    RuntimeIds,
    Transcript,
)
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.workspace import WorkspaceRepository


@pytest.fixture
def ws_repo(db):
    return WorkspaceRepository(db)


@pytest.fixture
def workspace(ws_repo):
    return ws_repo.create(tenant_id="tenant-1", name="Test Workspace")


@pytest.fixture
def adapter(db):
    return OpencodeAdapter(db)


# ------------------------------------------------------------------
# Class attributes
# ------------------------------------------------------------------

class TestOpencodeAdapterClass:
    def test_adapter_type(self):
        assert OpencodeAdapter.adapter_type == "opencode"

    def test_capabilities(self):
        caps = OpencodeAdapter.capabilities
        assert caps.can_stop is True
        assert caps.can_cancel is True
        assert caps.can_diff is True
        assert caps.can_shell is True
        assert caps.can_file_write is True
        assert caps.has_process_ids is True
        assert caps.can_replay is True
        assert caps.can_pause is False
        assert caps.can_steer is False
        assert caps.can_remote is False

    def test_capabilities_dict(self, adapter):
        d = adapter.capabilities_dict()
        assert isinstance(d, dict)
        assert d["can_stop"] is True
        assert d["can_diff"] is True


# ------------------------------------------------------------------
# start() — graceful degradation when opencode binary not installed
# ------------------------------------------------------------------

class TestOpencodeAdapterStartMissingBinary:
    def test_start_raises_connection_error_when_binary_missing(self, adapter, workspace):
        """OpencodeAdapter.start() raises ConnectionError when 'opencode' is not in PATH."""
        with patch("shutil.which", return_value=None):
            with pytest.raises(ConnectionError) as exc_info:
                adapter.start(
                    workspace_id=workspace.workspace_id,
                    session_id="session-1",
                    command="test task",
                )
            assert "opencode binary not found" in str(exc_info.value).lower()

    def test_start_does_not_create_harness_run_when_binary_missing(self, adapter, workspace):
        """No HarnessRun record is created when the binary is missing."""
        repo = HarnessRunRepository(adapter.conn)
        with patch("shutil.which", return_value=None):
            with pytest.raises(ConnectionError):
                adapter.start(
                    workspace_id=workspace.workspace_id,
                    session_id="session-1",
                    command="test task",
                )
        # No harness runs should exist for this session
        runs = repo.list_by_session("session-1")
        assert len(runs) == 0


# ------------------------------------------------------------------
# start() — successful path (mocked subprocess)
# ------------------------------------------------------------------

class TestOpencodeAdapterStartSuccess:
    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    @patch("subprocess.Popen")
    def test_start_creates_harness_run(self, mock_popen, mock_which, adapter, workspace):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        harness_run_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="implement feature",
        )

        assert harness_run_id is not None
        repo = HarnessRunRepository(adapter.conn)
        hr = repo.get_by_id(harness_run_id)
        assert hr is not None
        assert hr.harness_type == "opencode"
        assert hr.status == "running"
        assert hr.session_id == "session-1"
        assert hr.runtime_session_id == "session-1"
        assert hr.runtime_process_id == "12345"

    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    @patch("subprocess.Popen")
    def test_start_stores_capabilities(self, mock_popen, mock_which, adapter, workspace):
        mock_proc = MagicMock()
        mock_proc.pid = 99
        mock_popen.return_value = mock_proc

        harness_run_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="test",
        )

        repo = HarnessRunRepository(adapter.conn)
        hr = repo.get_by_id(harness_run_id)
        assert hr.control_capabilities_json is not None

    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    @patch("subprocess.Popen")
    def test_start_registers_session(self, mock_popen, mock_which, adapter, workspace):
        mock_proc = MagicMock()
        mock_proc.pid = 77
        mock_popen.return_value = mock_proc

        harness_run_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="test",
        )

        assert harness_run_id in adapter._sessions
        session_info = adapter._sessions[harness_run_id]
        assert session_info["session_id"] == "session-1"
        assert session_info["process"].pid == 77

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_start_honors_env_path_for_binary_lookup(self, mock_which, mock_popen, adapter, workspace):
        mock_proc = MagicMock()
        mock_proc.pid = 88
        mock_popen.return_value = mock_proc

        def which_side_effect(binary, path=None):
            assert binary == "opencode"
            assert path == "/custom/opencode/bin:/usr/bin"
            return "/custom/opencode/bin/opencode"

        mock_which.side_effect = which_side_effect

        harness_run_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="test",
            env={"PATH": "/custom/opencode/bin:/usr/bin"},
        )

        assert harness_run_id is not None
        repo = HarnessRunRepository(adapter.conn)
        hr = repo.get_by_id(harness_run_id)
        assert hr is not None
        assert hr.runtime_process_id == "88"


# ------------------------------------------------------------------
# stop()
# ------------------------------------------------------------------

class TestOpencodeAdapterStop:
    def test_stop_raises_when_no_session(self, adapter):
        with pytest.raises(HarnessNotReadyError):
            adapter.stop("nonexistent-id")

    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    @patch("subprocess.Popen")
    def test_stop_updates_status_to_completed(self, mock_popen, mock_which, adapter, workspace):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        harness_run_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="test",
        )

        adapter.stop(harness_run_id)

        mock_proc.terminate.assert_called_once()

        repo = HarnessRunRepository(adapter.conn)
        hr = repo.get_by_id(harness_run_id)
        assert hr.status == "completed"
        assert hr.ended_at is not None

    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    @patch("subprocess.Popen")
    def test_stop_already_exited(self, mock_popen, mock_which, adapter, workspace):
        mock_proc = MagicMock()
        mock_proc.pid = 101
        mock_proc.poll.return_value = 0  # already exited
        mock_popen.return_value = mock_proc

        harness_run_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="test",
        )

        adapter.stop(harness_run_id)

        mock_proc.terminate.assert_not_called()
        repo = HarnessRunRepository(adapter.conn)
        hr = repo.get_by_id(harness_run_id)
        assert hr.status == "completed"


# ------------------------------------------------------------------
# cancel()
# ------------------------------------------------------------------

class TestOpencodeAdapterCancel:
    def test_cancel_raises_when_no_session(self, adapter):
        with pytest.raises(HarnessNotReadyError):
            adapter.cancel("nonexistent-id")

    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    @patch("subprocess.Popen")
    def test_cancel_kills_process(self, mock_popen, mock_which, adapter, workspace):
        mock_proc = MagicMock()
        mock_proc.pid = 200
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        harness_run_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="test",
        )

        adapter.cancel(harness_run_id)

        mock_proc.kill.assert_called_once()

        repo = HarnessRunRepository(adapter.conn)
        hr = repo.get_by_id(harness_run_id)
        assert hr.status == "cancelled"


# ------------------------------------------------------------------
# get_runtime_ids()
# ------------------------------------------------------------------

class TestOpencodeAdapterGetRuntimeIds:
    def test_get_runtime_ids_empty_when_no_session(self, adapter):
        ids = adapter.get_runtime_ids("nonexistent")
        assert ids.session_id is None
        assert ids.process_id is None

    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    @patch("subprocess.Popen")
    def test_get_runtime_ids_returns_ids(self, mock_popen, mock_which, adapter, workspace):
        mock_proc = MagicMock()
        mock_proc.pid = 300
        mock_popen.return_value = mock_proc

        harness_run_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="test",
        )

        ids = adapter.get_runtime_ids(harness_run_id)
        assert isinstance(ids, RuntimeIds)
        assert ids.session_id == "session-1"
        assert ids.process_id == "300"


# ------------------------------------------------------------------
# get_transcript()
# ------------------------------------------------------------------

class TestOpencodeAdapterGetTranscript:
    def test_get_transcript_empty_when_no_session(self, adapter):
        t = adapter.get_transcript("nonexistent")
        assert isinstance(t, Transcript)
        assert t.stdout == ""

    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    @patch("subprocess.Popen")
    def test_transcript_captures_subprocess_output(
        self, mock_popen, mock_which, adapter, workspace
    ):
        """Regression: the daemon reader must populate the transcript
        from the opencode subprocess stdout (previously always empty)."""
        proc = MagicMock()
        proc.pid = 321
        proc.poll.return_value = None
        proc.communicate.return_value = ("opencode-stdout-marker\n", "")
        mock_popen.return_value = proc

        hr_id = adapter.start(
            workspace_id=workspace.workspace_id,
            session_id="session-1",
            command="say hi",
        )

        # The daemon reader runs in a thread; give it a moment to
        # drain communicate().
        import time
        for _ in range(40):
            t = adapter.get_transcript(hr_id)
            if "opencode-stdout-marker" in t.stdout:
                break
            time.sleep(0.05)

        t = adapter.get_transcript(hr_id)
        assert "opencode-stdout-marker" in t.stdout


# ------------------------------------------------------------------
# get_diff()
# ------------------------------------------------------------------

class TestOpencodeAdapterGetDiff:
    def test_get_diff_empty_when_no_session(self, adapter):
        assert adapter.get_diff("nonexistent") == ""

    def test_get_diff_returns_stored_diff(self, adapter, workspace):
        # Manually inject a session to test diff retrieval
        harness_run_id = "manual-id"
        adapter._sessions[harness_run_id] = {
            "process": MagicMock(),
            "session_id": "session-1",
            "stdout": "",
            "stderr": "",
            "diff": "--- a/file.py\n+++ b/file.py\n",
            "command": "test",
        }
        diff = adapter.get_diff(harness_run_id)
        assert "--- a/file.py" in diff
