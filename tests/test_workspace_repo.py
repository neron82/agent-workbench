"""Tests for WorkspaceRepository."""

import pytest

from agent_workbench.models.workspace import Workspace, WorkspaceRepository


@pytest.fixture
def repo(db):
    return WorkspaceRepository(db)


class TestWorkspaceCreate:
    def test_create_returns_workspace_dataclass(self, repo):
        ws = repo.create(tenant_id="tenant-1", name="My Workspace")
        assert isinstance(ws, Workspace)
        assert ws.tenant_id == "tenant-1"
        assert ws.name == "My Workspace"
        assert ws.is_default is False
        assert ws.workspace_id is not None
        assert ws.created_at > 0

    def test_create_generates_unique_ids(self, repo):
        ws1 = repo.create(tenant_id="t1", name="A")
        ws2 = repo.create(tenant_id="t1", name="B")
        assert ws1.workspace_id != ws2.workspace_id

    def test_create_with_is_default(self, repo):
        ws = repo.create(tenant_id="t1", name="Default WS", is_default=True)
        assert ws.is_default is True


class TestWorkspaceGetById:
    def test_get_existing(self, repo):
        ws = repo.create(tenant_id="t1", name="Find Me")
        found = repo.get_by_id(ws.workspace_id)
        assert found is not None
        assert found.workspace_id == ws.workspace_id
        assert found.name == "Find Me"

    def test_get_nonexistent(self, repo):
        assert repo.get_by_id("nonexistent") is None


class TestWorkspaceGetDefault:
    def test_get_default_for_tenant(self, repo):
        repo.create(tenant_id="t1", name="Not Default", is_default=False)
        ws = repo.create(tenant_id="t1", name="The Default", is_default=True)
        found = repo.get_default("t1")
        assert found is not None
        assert found.workspace_id == ws.workspace_id

    def test_no_default_returns_none(self, repo):
        repo.create(tenant_id="t1", name="Only One", is_default=False)
        assert repo.get_default("t1") is None

    def test_default_scoped_to_tenant(self, repo):
        repo.create(tenant_id="t1", name="Default T1", is_default=True)
        assert repo.get_default("t2") is None


class TestWorkspaceListAll:
    def test_list_empty(self, repo):
        assert repo.list_all() == []

    def test_list_multiple(self, repo):
        repo.create(tenant_id="t1", name="A")
        repo.create(tenant_id="t1", name="B")
        all_ws = repo.list_all()
        assert len(all_ws) == 2
        assert all(isinstance(w, Workspace) for w in all_ws)


class TestWorkspaceUpdate:
    def test_update_name(self, repo):
        ws = repo.create(tenant_id="t1", name="Old Name")
        updated = repo.update(ws.workspace_id, name="New Name")
        assert updated is not None
        assert updated.name == "New Name"

    def test_update_is_default(self, repo):
        ws = repo.create(tenant_id="t1", name="W", is_default=False)
        updated = repo.update(ws.workspace_id, is_default=True)
        assert updated is not None
        assert updated.is_default is True

    def test_update_nonexistent(self, repo):
        assert repo.update("nonexistent", name="X") is None

    def test_update_no_changes_returns_existing(self, repo):
        ws = repo.create(tenant_id="t1", name="W")
        result = repo.update(ws.workspace_id)
        assert result is not None
        assert result.workspace_id == ws.workspace_id

    def test_update_preserves_tenant_id(self, repo):
        ws = repo.create(tenant_id="t1", name="W")
        updated = repo.update(ws.workspace_id, name="W2")
        assert updated.tenant_id == "t1"


class TestWorkspaceDelete:
    def test_delete_existing(self, repo):
        ws = repo.create(tenant_id="t1", name="Delete Me")
        assert repo.delete(ws.workspace_id) is True
        assert repo.get_by_id(ws.workspace_id) is None

    def test_delete_nonexistent(self, repo):
        assert repo.delete("nonexistent") is False
