"""AssetService — project-scoped asset management with path safety."""

from __future__ import annotations

import os
import sqlite3
from typing import List, Optional

from agent_workbench.models.project_asset import (
    ASSET_TYPES,
    ProjectAsset,
    ProjectAssetRepository,
)


class AssetService:
    """Service for managing project-scoped assets (directories, repos, files).

    Rejects unsafe asset paths:
    - Absolute paths (must be relative to the internal workspace)
    - Path traversal attempts (``..`` components that escape the workspace)
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.repo = ProjectAssetRepository(conn)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_asset(
        self,
        *,
        workspace_id: str,
        asset_type: str,
        path: str,
        label: str = "",
        description: str = "",
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> ProjectAsset:
        """Add a new project asset, validating the path.

        Raises
        ------
        ValueError
            If the path is absolute, traverses out of the workspace, or
            the asset_type is invalid.
        """
        self._validate_path(path)
        if asset_type not in ASSET_TYPES:
            raise ValueError(
                f"Invalid asset_type: {asset_type!r}. Must be one of {ASSET_TYPES}"
            )
        return self.repo.create(
            workspace_id=workspace_id,
            asset_type=asset_type,
            path=path,
            label=label or os.path.basename(path),
            description=description,
            session_id=session_id,
            agent_id=agent_id,
        )

    def get_asset(self, asset_id: str) -> ProjectAsset:
        asset = self.repo.get_by_id(asset_id)
        if asset is None:
            raise LookupError(f"Asset not found: {asset_id!r}")
        return asset

    def list_assets(
        self, workspace_id: str, asset_type: Optional[str] = None
    ) -> List[ProjectAsset]:
        return self.repo.list_by_workspace(workspace_id, asset_type=asset_type)

    def list_assets_for_session(self, session_id: str) -> List[ProjectAsset]:
        return self.repo.list_by_session(session_id)

    def update_asset(
        self,
        asset_id: str,
        *,
        label: Optional[str] = None,
        description: Optional[str] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> ProjectAsset:
        asset = self.repo.get_by_id(asset_id)
        if asset is None:
            raise LookupError(f"Asset not found: {asset_id!r}")
        updated = self.repo.update(
            asset_id,
            label=label,
            description=description,
            session_id=session_id,
            agent_id=agent_id,
        )
        if updated is None:
            raise LookupError(f"Asset not found: {asset_id!r}")
        return updated

    def remove_asset(self, asset_id: str) -> None:
        if not self.repo.delete(asset_id):
            raise LookupError(f"Asset not found: {asset_id!r}")

    # ------------------------------------------------------------------
    # Path safety
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_path(path: str) -> None:
        """Validate a linked root and reject relative traversal.

        Absolute paths are valid explicit local roots; browser navigation is
        separately confined to the resolved root.
        """
        if not path:
            raise ValueError("Asset path must not be empty")
        if "\x00" in path:
            raise ValueError("Asset path must not contain NUL bytes")

        # Relative paths must stay inside the workspace-relative namespace.
        if os.path.isabs(path):
            return
        normalized = os.path.normpath(path).replace("\\", "/")
        parts = normalized.split("/")
        if any(part == ".." for part in parts):
            raise ValueError(
                f"Path traversal (..) is not allowed: {path!r}"
            )
