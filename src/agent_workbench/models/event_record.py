"""Event record domain model and repository."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class EventRecord:
    event_id: str
    harness_run_id: Optional[str]
    routed_message_id: Optional[str]
    event_type: str
    event_source: str
    event_payload_ref: Optional[str]
    event_ts: float


class EventRecordRepository:
    """SQLite-backed repository for EventRecord entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        event_type: str,
        event_source: str,
        harness_run_id: Optional[str] = None,
        routed_message_id: Optional[str] = None,
        event_payload_ref: Optional[str] = None,
        event_ts: Optional[float] = None,
    ) -> EventRecord:
        """Insert a new event record and return the persisted instance."""
        event_id = uuid.uuid4().hex
        ts = event_ts if event_ts is not None else time.time()
        self.conn.execute(
            "INSERT INTO event_records "
            "(event_id, harness_run_id, routed_message_id, "
            "event_type, event_source, event_payload_ref, event_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                harness_run_id,
                routed_message_id,
                event_type,
                event_source,
                event_payload_ref,
                ts,
            ),
        )
        self.conn.commit()
        return self.get_by_id(event_id)

    def get_by_id(self, event_id: str) -> Optional[EventRecord]:
        row = self.conn.execute(
            "SELECT event_id, harness_run_id, routed_message_id, "
            "event_type, event_source, event_payload_ref, event_ts "
            "FROM event_records WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_harness_run(self, harness_run_id: str) -> List[EventRecord]:
        rows = self.conn.execute(
            "SELECT event_id, harness_run_id, routed_message_id, "
            "event_type, event_source, event_payload_ref, event_ts "
            "FROM event_records WHERE harness_run_id = ? ORDER BY event_ts ASC",
            (harness_run_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_by_routed_message(self, routed_message_id: str) -> List[EventRecord]:
        rows = self.conn.execute(
            "SELECT event_id, harness_run_id, routed_message_id, "
            "event_type, event_source, event_payload_ref, event_ts "
            "FROM event_records WHERE routed_message_id = ? ORDER BY event_ts ASC",
            (routed_message_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, event_id: str) -> bool:
        """Delete an event record. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM event_records WHERE event_id = ?",
            (event_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            event_id=row["event_id"],
            harness_run_id=row["harness_run_id"],
            routed_message_id=row["routed_message_id"],
            event_type=row["event_type"],
            event_source=row["event_source"],
            event_payload_ref=row["event_payload_ref"],
            event_ts=row["event_ts"],
        )
