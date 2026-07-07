"""Tests for RoleService."""

import pytest

from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.role_service import (
    RoleInUseError,
    RoleNotFoundError,
    RoleService,
)


@pytest.fixture
def svc(db):
    return RoleService(db)


class TestCreateRole:
    def test_create_and_get(self, svc):
        role = svc.create_role(
            name="summarizer",
            description="Summarizes threads",
            system_prompt="Summarize clearly.",
        )
        fetched = svc.get_role(role.role_id)
        assert fetched.name == "summarizer"
        assert fetched.system_prompt == "Summarize clearly."


class TestUpdateRole:
    def test_update_fields(self, svc):
        role = svc.create_role(name="helper")
        updated = svc.update_role(
            role.role_id,
            description="Now descriptive",
            system_prompt="Be direct.",
        )
        assert updated.description == "Now descriptive"
        assert updated.system_prompt == "Be direct."


class TestDeleteRole:
    def test_delete_builtin_raises(self, svc):
        builtin = next(role for role in svc.list_roles() if role.is_builtin)
        with pytest.raises(RoleInUseError):
            svc.delete_role(builtin.role_id)

    def test_delete_in_use_role_raises(self, db, svc):
        role = svc.create_role(name="temp-role")
        ProfileService(db).create_profile(
            name="agent-b",
            provider="provider-mock-default",
            function=role.role_id,
            model="mock-model",
        )
        with pytest.raises(RoleInUseError):
            svc.delete_role(role.role_id)
