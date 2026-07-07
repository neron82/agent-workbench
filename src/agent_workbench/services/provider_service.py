"""ProviderService — CRUD plus usage safety checks."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from agent_workbench.models.provider import Provider, ProviderRepository


class ProviderNotFoundError(LookupError):
    """Raised when a provider cannot be found."""


class ProviderInUseError(ValueError):
    """Raised when deleting a provider that active agent profiles reference."""


class ProviderService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.providers = ProviderRepository(conn)

    def create_provider(
        self,
        *,
        name: str,
        provider_kind: str,
        endpoint_url: Optional[str] = None,
        api_key_env_var: Optional[str] = None,
        default_model: Optional[str] = None,
        config_json: Optional[Dict[str, Any]] = None,
        is_enabled: bool = True,
    ) -> Provider:
        return self.providers.create(
            name=name,
            provider_kind=provider_kind,
            endpoint_url=endpoint_url,
            api_key_env_var=api_key_env_var,
            default_model=default_model,
            config_json=config_json,
            is_enabled=is_enabled,
        )

    def get_provider(self, provider_id: str) -> Provider:
        provider = self.providers.get_by_id(provider_id)
        if provider is None:
            raise ProviderNotFoundError(f"Provider not found: {provider_id!r}")
        return provider

    def list_providers(self) -> List[Provider]:
        return self.providers.list_all()

    def update_provider(self, provider_id: str, **fields: Any) -> Provider:
        updated = self.providers.update(provider_id, **fields)
        if updated is None:
            raise ProviderNotFoundError(f"Provider not found: {provider_id!r}")
        return updated

    def delete_provider(self, provider_id: str) -> None:
        refs = self.conn.execute(
            "SELECT COUNT(*) AS n FROM agent_profiles WHERE provider_ref = ?",
            (provider_id,),
        ).fetchone()["n"]
        if refs:
            raise ProviderInUseError(
                f"Cannot delete provider {provider_id!r}: {refs} agent profile(s) reference it."
            )
        if not self.providers.delete(provider_id):
            raise ProviderNotFoundError(f"Provider not found: {provider_id!r}")
