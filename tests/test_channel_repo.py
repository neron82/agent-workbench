"""Tests for ChannelRepository."""

import pytest

from agent_workbench.models.channel import Channel, ChannelRepository
from agent_workbench.models.workspace import WorkspaceRepository


@pytest.fixture
def workspace_id(db):
    repo = WorkspaceRepository(db)
    ws = repo.create(tenant_id="tenant-1", name="Test Workspace")
    return ws.workspace_id


@pytest.fixture
def channel_repo(db):
    return ChannelRepository(db)


class TestChannelCreate:
    def test_create_returns_channel_dataclass(self, channel_repo, workspace_id):
        ch = channel_repo.create(
            workspace_id=workspace_id,
            channel_kind="chat",
            title="General",
        )
        assert isinstance(ch, Channel)
        assert ch.workspace_id == workspace_id
        assert ch.channel_kind == "chat"
        assert ch.title == "General"
        assert ch.status == "active"
        assert ch.channel_id is not None
        assert ch.created_at > 0
        assert ch.updated_at > 0

    def test_create_all_kinds(self, channel_repo, workspace_id):
        for kind in ("chat", "research", "work", "review", "system"):
            ch = channel_repo.create(workspace_id=workspace_id, channel_kind=kind)
            assert ch.channel_kind == kind

    def test_create_invalid_kind_raises(self, channel_repo, workspace_id):
        with pytest.raises(ValueError, match="Invalid channel_kind"):
            channel_repo.create(
                workspace_id=workspace_id, channel_kind="invalid"
            )

    def test_create_with_optional_fields(self, channel_repo, workspace_id):
        ch = channel_repo.create(
            workspace_id=workspace_id,
            channel_kind="work",
            title="Project",
            active_session_id="sess-1",
            default_target="agent-x",
            status="paused",
        )
        assert ch.active_session_id == "sess-1"
        assert ch.default_target == "agent-x"
        assert ch.status == "paused"

    def test_create_invalid_status_raises(self, channel_repo, workspace_id):
        with pytest.raises(ValueError, match="Invalid status"):
            channel_repo.create(
                workspace_id=workspace_id,
                channel_kind="chat",
                status="invalid",
            )


class TestChannelGetById:
    def test_get_existing(self, channel_repo, workspace_id):
        ch = channel_repo.create(
            workspace_id=workspace_id, channel_kind="chat", title="Find Me"
        )
        found = channel_repo.get_by_id(ch.channel_id)
        assert found is not None
        assert found.channel_id == ch.channel_id
        assert found.title == "Find Me"

    def test_get_nonexistent(self, channel_repo):
        assert channel_repo.get_by_id("nonexistent") is None


class TestChannelListByWorkspace:
    def test_list_empty(self, channel_repo, workspace_id):
        assert channel_repo.list_by_workspace(workspace_id) == []

    def test_list_multiple(self, channel_repo, workspace_id):
        channel_repo.create(workspace_id=workspace_id, channel_kind="chat")
        channel_repo.create(workspace_id=workspace_id, channel_kind="work")
        channels = channel_repo.list_by_workspace(workspace_id)
        assert len(channels) == 2
        assert all(isinstance(c, Channel) for c in channels)

    def test_list_scoped_to_workspace(self, channel_repo, db):
        ws_repo = WorkspaceRepository(db)
        ws1 = ws_repo.create(tenant_id="t1", name="WS1")
        ws2 = ws_repo.create(tenant_id="t1", name="WS2")
        channel_repo.create(workspace_id=ws1.workspace_id, channel_kind="chat")
        channel_repo.create(workspace_id=ws2.workspace_id, channel_kind="work")
        assert len(channel_repo.list_by_workspace(ws1.workspace_id)) == 1
        assert len(channel_repo.list_by_workspace(ws2.workspace_id)) == 1


class TestChannelUpdateStatus:
    def test_update_status(self, channel_repo, workspace_id):
        ch = channel_repo.create(
            workspace_id=workspace_id, channel_kind="chat", status="active"
        )
        updated = channel_repo.update_status(ch.channel_id, status="paused")
        assert updated is not None
        assert updated.status == "paused"

    def test_update_status_all_values(self, channel_repo, workspace_id):
        ch = channel_repo.create(workspace_id=workspace_id, channel_kind="chat")
        for status in ("active", "paused", "stopped", "archived"):
            updated = channel_repo.update_status(ch.channel_id, status=status)
            assert updated.status == status

    def test_update_status_invalid_raises(self, channel_repo, workspace_id):
        ch = channel_repo.create(workspace_id=workspace_id, channel_kind="chat")
        with pytest.raises(ValueError, match="Invalid status"):
            channel_repo.update_status(ch.channel_id, status="invalid")

    def test_update_status_nonexistent(self, channel_repo):
        assert channel_repo.update_status("nonexistent", status="paused") is None


class TestChannelUpdateActiveSession:
    def test_update_active_session(self, channel_repo, workspace_id):
        ch = channel_repo.create(workspace_id=workspace_id, channel_kind="chat")
        updated = channel_repo.update_active_session(
            ch.channel_id, active_session_id="sess-99"
        )
        assert updated is not None
        assert updated.active_session_id == "sess-99"

    def test_clear_active_session(self, channel_repo, workspace_id):
        ch = channel_repo.create(
            workspace_id=workspace_id,
            channel_kind="chat",
            active_session_id="sess-1",
        )
        updated = channel_repo.update_active_session(
            ch.channel_id, active_session_id=None
        )
        assert updated is not None
        assert updated.active_session_id is None

    def test_update_active_session_nonexistent(self, channel_repo):
        assert channel_repo.update_active_session(
            "nonexistent", active_session_id="sess-1"
        ) is None


class TestChannelDelete:
    def test_delete_existing(self, channel_repo, workspace_id):
        ch = channel_repo.create(workspace_id=workspace_id, channel_kind="chat")
        assert channel_repo.delete(ch.channel_id) is True
        assert channel_repo.get_by_id(ch.channel_id) is None

    def test_delete_nonexistent(self, channel_repo):
        assert channel_repo.delete("nonexistent") is False
