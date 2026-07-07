"""Tests for SessionExtensionRepository."""

import pytest

from agent_workbench.models.session_extension import (
    SessionExtension,
    SessionExtensionRepository,
)
from agent_workbench.models.workspace import WorkspaceRepository


@pytest.fixture
def workspace_id(db):
    repo = WorkspaceRepository(db)
    ws = repo.create(tenant_id="tenant-1", name="Test Workspace")
    return ws.workspace_id


@pytest.fixture
def ext_repo(db):
    return SessionExtensionRepository(db)


class TestSessionExtensionCreate:
    def test_create_returns_dataclass(self, ext_repo, workspace_id):
        ext = ext_repo.create(
            workspace_id=workspace_id,
            session_type="chat",
        )
        assert isinstance(ext, SessionExtension)
        assert ext.workspace_id == workspace_id
        assert ext.session_type == "chat"
        assert ext.status == "active"
        assert ext.session_id is not None
        assert ext.created_at > 0
        assert ext.agent_profile_binding_id is None
        assert ext.fork_id is None
        assert ext.task_spec_id is None

    def test_create_all_session_types(self, ext_repo, workspace_id):
        for stype in ("chat", "research", "work"):
            ext = ext_repo.create(workspace_id=workspace_id, session_type=stype)
            assert ext.session_type == stype

    def test_create_invalid_session_type_raises(self, ext_repo, workspace_id):
        with pytest.raises(ValueError, match="Invalid session_type"):
            ext_repo.create(workspace_id=workspace_id, session_type="invalid")

    def test_create_with_optional_fields(self, ext_repo, workspace_id):
        """FK fields (binding, fork, task_spec) require real DB rows — test
        nullable defaults + status instead."""
        ext = ext_repo.create(
            workspace_id=workspace_id,
            session_type="work",
            status="waiting_review",
        )
        assert ext.agent_profile_binding_id is None
        assert ext.fork_id is None
        assert ext.task_spec_id is None
        assert ext.status == "waiting_review"

    def test_create_invalid_status_raises(self, ext_repo, workspace_id):
        with pytest.raises(ValueError, match="Invalid status"):
            ext_repo.create(
                workspace_id=workspace_id,
                session_type="chat",
                status="invalid",
            )


class TestSessionExtensionGetById:
    def test_get_existing(self, ext_repo, workspace_id):
        ext = ext_repo.create(workspace_id=workspace_id, session_type="chat")
        found = ext_repo.get_by_id(ext.session_id)
        assert found is not None
        assert found.session_id == ext.session_id
        assert found.session_type == "chat"

    def test_get_nonexistent(self, ext_repo):
        assert ext_repo.get_by_id("nonexistent") is None


class TestSessionExtensionGetBySessionId:
    def test_alias_works(self, ext_repo, workspace_id):
        ext = ext_repo.create(workspace_id=workspace_id, session_type="research")
        found = ext_repo.get_by_session_id(ext.session_id)
        assert found is not None
        assert found.session_id == ext.session_id


class TestSessionExtensionUpdateStatus:
    def test_update_status(self, ext_repo, workspace_id):
        ext = ext_repo.create(workspace_id=workspace_id, session_type="chat")
        updated = ext_repo.update_status(ext.session_id, status="done")
        assert updated is not None
        assert updated.status == "done"

    def test_update_status_all_values(self, ext_repo, workspace_id):
        ext = ext_repo.create(workspace_id=workspace_id, session_type="chat")
        for status in (
            "active",
            "waiting_review",
            "waiting_approval",
            "done",
            "blocked",
            "failed",
            "archived",
        ):
            updated = ext_repo.update_status(ext.session_id, status=status)
            assert updated.status == status

    def test_update_status_invalid_raises(self, ext_repo, workspace_id):
        ext = ext_repo.create(workspace_id=workspace_id, session_type="chat")
        with pytest.raises(ValueError, match="Invalid status"):
            ext_repo.update_status(ext.session_id, status="invalid")

    def test_update_status_nonexistent(self, ext_repo):
        assert ext_repo.update_status("nonexistent", status="done") is None

    def test_session_type_immutable_via_update_status(self, ext_repo, workspace_id):
        ext = ext_repo.create(workspace_id=workspace_id, session_type="chat")
        ext_repo.update_status(ext.session_id, status="done")
        found = ext_repo.get_by_id(ext.session_id)
        assert found.session_type == "chat"


class TestSessionExtensionUpdateTaskSpec:
    def test_update_task_spec_to_null(self, ext_repo, workspace_id):
        """Setting task_spec_id to NULL is always valid (nullable FK)."""
        ext = ext_repo.create(workspace_id=workspace_id, session_type="work")
        updated = ext_repo.update_task_spec(
            ext.session_id, task_spec_id=None
        )
        assert updated is not None
        assert updated.task_spec_id is None

    def test_update_task_spec_nonexistent(self, ext_repo):
        assert ext_repo.update_task_spec(
            "nonexistent", task_spec_id=None
        ) is None


class TestSessionExtensionDelete:
    def test_delete_existing(self, ext_repo, workspace_id):
        ext = ext_repo.create(workspace_id=workspace_id, session_type="chat")
        assert ext_repo.delete(ext.session_id) is True
        assert ext_repo.get_by_id(ext.session_id) is None

    def test_delete_nonexistent(self, ext_repo):
        assert ext_repo.delete("nonexistent") is False
