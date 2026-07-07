"""RoleService — CRUD plus builtin-role safety checks."""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from agent_workbench.models.role import Role, RoleRepository


class RoleNotFoundError(LookupError):
    """Raised when a role cannot be found."""


class RoleInUseError(ValueError):
    """Raised when deleting a role that cannot be removed."""


class RoleService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.roles = RoleRepository(conn)

    def create_role(
        self,
        *,
        name: str,
        description: str = "",
        system_prompt: str = "",
        is_builtin: bool = False,
    ) -> Role:
        return self.roles.create(
            name=name,
            description=description,
            system_prompt=system_prompt,
            is_builtin=is_builtin,
        )

    def get_role(self, role_id: str) -> Role:
        role = self.roles.get_by_id(role_id)
        if role is None:
            raise RoleNotFoundError(f"Role not found: {role_id!r}")
        return role

    def list_roles(self) -> List[Role]:
        return self.roles.list_all()

    def update_role(
        self,
        role_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Role:
        updated = self.roles.update(
            role_id,
            name=name,
            description=description,
            system_prompt=system_prompt,
        )
        if updated is None:
            raise RoleNotFoundError(f"Role not found: {role_id!r}")
        return updated

    def delete_role(self, role_id: str) -> None:
        role = self.roles.get_by_id(role_id)
        if role is None:
            raise RoleNotFoundError(f"Role not found: {role_id!r}")
        if role.is_builtin:
            raise RoleInUseError(f"Cannot delete builtin role {role.name!r}.")
        refs = self.conn.execute(
            "SELECT COUNT(*) AS n FROM agent_profiles WHERE function_ref = ?",
            (role_id,),
        ).fetchone()["n"]
        if refs:
            raise RoleInUseError(
                f"Cannot delete role {role.name!r}: {refs} agent profile(s) reference it."
            )
        self.roles.delete(role_id)
