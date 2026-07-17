"""IdentityService — local user identity management."""

from __future__ import annotations

import sqlite3
from typing import Optional

from agent_workbench.models.user import User, UserRepository


class IdentityService:
    """Service for local user identity lifecycle.

    First visit creates a user record. Subsequent visits resolve by
    session-backed cookie id. Provides the stable ``user_id`` that
    replaces the old ``web-user`` fallback in message payloads.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.repo = UserRepository(conn)

    def get_or_create_user(self, user_id: Optional[str] = None, *, display_name: str = "") -> User:
        """Resolve an existing user or create a new one.

        When ``user_id`` is provided and exists, update display_name if
        non-empty and record the visit.  When ``user_id`` is ``None`` or
        unknown, create a new user.
        """
        if user_id is not None:
            user = self.repo.get_by_id(user_id)
            if user is not None:
                if display_name:
                    self.repo.update_display_name(user_id, display_name)
                else:
                    self.repo.record_seen(user_id)
                return self.repo.get_by_id(user_id)  # type: ignore[return-value]

        # Create new user
        return self.repo.create(display_name=display_name)

    def get_user(self, user_id: str) -> User:
        user = self.repo.get_by_id(user_id)
        if user is None:
            raise LookupError(f"User not found: {user_id!r}")
        return user

    def update_display_name(self, user_id: str, display_name: str) -> User:
        user = self.repo.update_display_name(user_id, display_name)
        if user is None:
            raise LookupError(f"User not found: {user_id!r}")
        return user