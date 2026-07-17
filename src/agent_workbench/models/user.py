"""User identity domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Optional


@dataclass
class User:
    user_id: str
    display_name: str
    created_at: float
    last_seen_at: float


class UserRepository:
    """SQLite-backed repository for local user identity records."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, *, display_name: str = "") -> User:
        user_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO users (user_id, display_name, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, display_name, now, now),
        )
        self.conn.commit()
        return self.get_by_id(user_id)  # type: ignore[return-value]

    def get_by_id(self, user_id: str) -> Optional[User]:
        row = self.conn.execute(
            "SELECT user_id, display_name, created_at, last_seen_at "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def update_display_name(self, user_id: str, display_name: str) -> Optional[User]:
        now = time.time()
        cursor = self.conn.execute(
            "UPDATE users SET display_name = ?, last_seen_at = ? WHERE user_id = ?",
            (display_name, now, user_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(user_id)

    def record_seen(self, user_id: str) -> Optional[User]:
        now = time.time()
        cursor = self.conn.execute(
            "UPDATE users SET last_seen_at = ? WHERE user_id = ?",
            (now, user_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(user_id)

    @staticmethod
    def _row(row: sqlite3.Row) -> User:
        return User(
            user_id=row["user_id"],
            display_name=row["display_name"],
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
        )