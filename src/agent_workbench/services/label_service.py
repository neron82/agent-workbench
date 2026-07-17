"""LabelService — session label management and lookup."""

from __future__ import annotations

import sqlite3
import time
from typing import List, Optional

from agent_workbench.models.session_label import (
    SessionLabel,
    SessionLabelRepository,
)


_BUILTIN_LABELS = {
    "chat": ("Chat", "#4A90D9", "General conversation and discussion."),
    "research": ("Research", "#7B61FF", "Evidence gathering and analysis."),
    "work": ("Work", "#E8922E", "Structured task execution."),
}


class LabelService:
    """High-level service for session label operations."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.repo = SessionLabelRepository(conn)

    def _ensure_builtins(self, workspace_id: str) -> None:
        """Seed builtin labels for a workspace if they don't exist yet."""
        for name, (display_name, color, description) in _BUILTIN_LABELS.items():
            existing = self.repo.get_by_name(workspace_id, name)
            if existing is not None:
                continue
            label_id = f"label-{workspace_id}-{name}"
            created_at = time.time()
            self.conn.execute(
                "INSERT INTO session_labels "
                "(label_id, workspace_id, name, display_name, color, description, is_builtin, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (label_id, workspace_id, name, display_name, color, description, created_at),
            )
        self.conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_label(
        self,
        *,
        workspace_id: str,
        name: str,
        display_name: str = "",
        color: str = "#4A90D9",
        description: str = "",
    ) -> SessionLabel:
        """Create a new custom label for a workspace.

        Builtin label names (chat, research, work) are auto-seeded
        on first access and cannot be re-created as custom labels.
        """
        self._ensure_builtins(workspace_id)
        if name in _BUILTIN_LABELS:
            existing = self.repo.get_by_name(workspace_id, name)
            if existing is not None:
                return existing
        return self.repo.create(
            workspace_id=workspace_id,
            name=name,
            display_name=display_name or name,
            color=color,
            description=description,
        )

    def get_label(self, label_id: str) -> SessionLabel:
        label = self.repo.get_by_id(label_id)
        if label is None:
            raise LookupError(f"Label not found: {label_id!r}")
        return label

    def get_label_by_name(self, workspace_id: str, name: str) -> Optional[SessionLabel]:
        self._ensure_builtins(workspace_id)
        return self.repo.get_by_name(workspace_id, name)

    def list_labels(self, workspace_id: str) -> List[SessionLabel]:
        self._ensure_builtins(workspace_id)
        return self.repo.list_by_workspace(workspace_id)

    def update_label(
        self,
        label_id: str,
        *,
        display_name: Optional[str] = None,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> SessionLabel:
        label = self.repo.get_by_id(label_id)
        if label is None:
            raise LookupError(f"Label not found: {label_id!r}")
        if label.is_builtin:
            raise ValueError("Builtin labels cannot be modified")
        updated = self.repo.update(
            label_id, display_name=display_name, color=color, description=description
        )
        if updated is None:
            raise LookupError(f"Label not found: {label_id!r}")
        return updated

    def delete_label(self, label_id: str) -> None:
        label = self.repo.get_by_id(label_id)
        if label is None:
            raise LookupError(f"Label not found: {label_id!r}")
        if label.is_builtin:
            raise ValueError("Builtin labels cannot be deleted")
        self.repo.delete(label_id)

    # ------------------------------------------------------------------
    # Lookup helpers for UI/runtime lanes
    # ------------------------------------------------------------------

    def get_label_display(self, workspace_id: str, name: str) -> dict:
        """Return display info for a label name, with fallback for unknown labels."""
        self._ensure_builtins(workspace_id)
        label = self.repo.get_by_name(workspace_id, name)
        if label is not None:
            return {
                "name": label.name,
                "display_name": label.display_name,
                "color": label.color,
                "description": label.description,
                "is_builtin": label.is_builtin,
            }
        # Fallback: generate a display from the name
        return {
            "name": name,
            "display_name": name.capitalize(),
            "color": "#888888",
            "description": "",
            "is_builtin": False,
        }