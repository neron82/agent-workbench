"""Tests for ProviderService."""

import pytest

from agent_workbench.services.provider_service import (
    ProviderInUseError,
    ProviderNotFoundError,
    ProviderService,
)
from agent_workbench.services.profile_service import ProfileService


@pytest.fixture
def svc(db):
    return ProviderService(db)


class TestCreateProvider:
    def test_create_and_get(self, svc):
        provider = svc.create_provider(
            name="Local Mock",
            provider_kind="mock",
            default_model="mock-model",
        )
        fetched = svc.get_provider(provider.provider_id)
        assert fetched.name == "Local Mock"
        assert fetched.provider_kind == "mock"


class TestUpdateProvider:
    def test_update_fields(self, svc):
        provider = svc.create_provider(name="Local Mock", provider_kind="mock")
        updated = svc.update_provider(
            provider.provider_id,
            name="Renamed Mock",
            config_json={"temperature": 0.2},
            is_enabled=False,
        )
        assert updated.name == "Renamed Mock"
        assert updated.config_json == {"temperature": 0.2}
        assert updated.is_enabled is False


class TestDeleteProvider:
    def test_delete_free_provider(self, svc):
        provider = svc.create_provider(name="Disposable", provider_kind="mock")
        svc.delete_provider(provider.provider_id)
        with pytest.raises(ProviderNotFoundError):
            svc.get_provider(provider.provider_id)

    def test_delete_in_use_provider_raises(self, db, svc):
        provider = svc.create_provider(name="Sticky", provider_kind="mock")
        ProfileService(db).create_profile(
            name="agent-a",
            provider=provider.provider_id,
            function="role-assistant",
            model="mock-model",
        )
        with pytest.raises(ProviderInUseError):
            svc.delete_provider(provider.provider_id)
