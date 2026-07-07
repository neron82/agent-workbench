"""Provider domain model and repository."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


PROVIDER_KINDS = ("mock", "openai_compatible")


@dataclass
class Provider:
    provider_id: str
    name: str
    provider_kind: str
    endpoint_url: Optional[str]
    api_key_env_var: Optional[str]
    default_model: Optional[str]
    config_json: Optional[Dict[str, Any]]
    is_enabled: bool
    created_at: float
    updated_at: float


class ProviderRepository:
    """SQLite-backed repository for Provider entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
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
        if provider_kind not in PROVIDER_KINDS:
            raise ValueError(
                f"Invalid provider_kind: {provider_kind!r}. Must be one of {PROVIDER_KINDS}"
            )
        provider_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO providers "
            "(provider_id, name, provider_kind, endpoint_url, api_key_env_var, "
            "default_model, config_json, is_enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                provider_id,
                name,
                provider_kind,
                endpoint_url,
                api_key_env_var,
                default_model,
                json.dumps(config_json) if config_json is not None else None,
                1 if is_enabled else 0,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get_by_id(provider_id)  # type: ignore[return-value]

    def get_by_id(self, provider_id: str) -> Optional[Provider]:
        row = self.conn.execute(
            "SELECT provider_id, name, provider_kind, endpoint_url, api_key_env_var, "
            "default_model, config_json, is_enabled, created_at, updated_at "
            "FROM providers WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_all(self) -> List[Provider]:
        rows = self.conn.execute(
            "SELECT provider_id, name, provider_kind, endpoint_url, api_key_env_var, "
            "default_model, config_json, is_enabled, created_at, updated_at "
            "FROM providers ORDER BY updated_at DESC, name ASC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def update(
        self,
        provider_id: str,
        *,
        name: Optional[str] = None,
        provider_kind: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        api_key_env_var: Optional[str] = None,
        default_model: Optional[str] = None,
        config_json: Optional[Dict[str, Any]] = None,
        is_enabled: Optional[bool] = None,
    ) -> Optional[Provider]:
        existing = self.get_by_id(provider_id)
        if existing is None:
            return None
        next_kind = provider_kind or existing.provider_kind
        if next_kind not in PROVIDER_KINDS:
            raise ValueError(
                f"Invalid provider_kind: {next_kind!r}. Must be one of {PROVIDER_KINDS}"
            )
        now = time.time()
        self.conn.execute(
            "UPDATE providers SET name = ?, provider_kind = ?, endpoint_url = ?, "
            "api_key_env_var = ?, default_model = ?, config_json = ?, is_enabled = ?, "
            "updated_at = ? WHERE provider_id = ?",
            (
                name if name is not None else existing.name,
                next_kind,
                endpoint_url if endpoint_url is not None else existing.endpoint_url,
                api_key_env_var if api_key_env_var is not None else existing.api_key_env_var,
                default_model if default_model is not None else existing.default_model,
                json.dumps(config_json) if config_json is not None else (
                    json.dumps(existing.config_json) if existing.config_json is not None else None
                ),
                1 if (is_enabled if is_enabled is not None else existing.is_enabled) else 0,
                now,
                provider_id,
            ),
        )
        self.conn.commit()
        return self.get_by_id(provider_id)

    def delete(self, provider_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM providers WHERE provider_id = ?",
            (provider_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> Provider:
        raw_config = row["config_json"]
        return Provider(
            provider_id=row["provider_id"],
            name=row["name"],
            provider_kind=row["provider_kind"],
            endpoint_url=row["endpoint_url"],
            api_key_env_var=row["api_key_env_var"],
            default_model=row["default_model"],
            config_json=json.loads(raw_config) if raw_config else None,
            is_enabled=bool(row["is_enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
