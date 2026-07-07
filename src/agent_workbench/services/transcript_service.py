"""Persistent transcript service for harness runs.

Why this exists
---------------
Before this module, transcripts lived only in the adapter's in-memory
``self._sessions[run_id]["stdout"]`` dict, which was lost on server
restart.  This service persists every transcript line to
``harness_transcripts`` and every lifecycle event to ``harness_events``
immediately, so that a run-detail page can show the real transcript even
after the workbench has been bounced.

Public surface
--------------
- ``TranscriptService.append(harness_run_id, stream, content)``
- ``TranscriptService.list(harness_run_id)``
- ``TranscriptService.append_event(harness_run_id, event_type, detail)``
- ``TranscriptService.list_events(harness_run_id)``
- ``TranscriptService.record_exit(harness_run_id, returncode, signal)``

All methods take a sqlite3 connection to keep the service transactionally
aligned with the caller.  Long-running adapters that need to write from a
daemon thread should open their own connection via ``get_connection``
(that's what ``ShellAdapter._collect_output`` already does).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional


class TranscriptService:
    """Thin repository over the ``harness_transcripts`` and
    ``harness_events`` tables."""

    # ------------------------------------------------------------------
    # Transcripts
    # ------------------------------------------------------------------

    def append(
        self,
        conn,
        *,
        harness_run_id: str,
        stream: str,
        content: str,
        line_no: Optional[int] = None,
        captured_at: Optional[float] = None,
    ) -> str:
        """Persist a single transcript line.

        Returns the generated ``transcript_id``.
        """
        if stream not in ("stdout", "stderr"):
            raise ValueError(f"invalid stream: {stream!r}")
        transcript_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO harness_transcripts
                (transcript_id, harness_run_id, line_no, stream, content, captured_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                transcript_id,
                harness_run_id,
                -1 if line_no is None else int(line_no),
                stream,
                content,
                captured_at if captured_at is not None else time.time(),
            ),
        )
        return transcript_id

    def list(
        self,
        conn,
        *,
        harness_run_id: str,
    ) -> List[Dict[str, Any]]:
        """Return all transcript rows for a run, ordered by ``captured_at``."""
        rows = conn.execute(
            """
            SELECT transcript_id, line_no, stream, content, captured_at
            FROM harness_transcripts
            WHERE harness_run_id = ?
            ORDER BY captured_at ASC, line_no ASC
            """,
            (harness_run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self, conn, *, harness_run_id: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM harness_transcripts WHERE harness_run_id = ?",
            (harness_run_id,),
        ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------
    # Lifecycle events
    # ------------------------------------------------------------------

    def append_event(
        self,
        conn,
        *,
        harness_run_id: str,
        event_type: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Persist a lifecycle event for the run."""
        if event_type not in (
            "start", "status_change", "transcript_flush",
            "stop", "cancel", "exit",
        ):
            raise ValueError(f"invalid event_type: {event_type!r}")
        event_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO harness_events
                (event_id, harness_run_id, event_type, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_id,
                harness_run_id,
                event_type,
                json.dumps(detail or {}, ensure_ascii=False),
                time.time(),
            ),
        )
        return event_id

    def list_events(
        self,
        conn,
        *,
        harness_run_id: str,
    ) -> List[Dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT event_id, event_type, detail_json, created_at
            FROM harness_events
            WHERE harness_run_id = ?
            ORDER BY created_at ASC
            """,
            (harness_run_id,),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d.pop("detail_json") or "{}")
            except json.JSONDecodeError:
                d["detail"] = {}
            out.append(d)
        return out

    # ------------------------------------------------------------------
    # Exit recording
    # ------------------------------------------------------------------

    def record_exit(
        self,
        conn,
        *,
        harness_run_id: str,
        returncode: Optional[int],
        signal: Optional[int] = None,
    ) -> None:
        """Record the final exit code / signal on ``harness_runs``."""
        conn.execute(
            """
            UPDATE harness_runs
            SET exit_code = ?, exit_signal = ?
            WHERE harness_run_id = ?
            """,
            (returncode, signal, harness_run_id),
        )
        detail = {"returncode": returncode, "signal": signal}
        self.append_event(
            conn,
            harness_run_id=harness_run_id,
            event_type="exit",
            detail=detail,
        )


# ----------------------------------------------------------------------
# Tiny helper for adapters that need the process group id of a subprocess.
# ----------------------------------------------------------------------

def pgid_of(proc) -> Optional[int]:
    """Return ``os.getpgid(proc.pid)`` or None if the process is gone."""
    if proc is None or proc.pid is None:
        return None
    try:
        return os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        return None
