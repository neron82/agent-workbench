"""Shared utilities for harness adapters.

Consolidates duplicated code across shell.py, hermes_adapter.py, and
opencode.py: output collection, process-group termination, and DB
path resolution.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from agent_workbench.db import get_connection
from agent_workbench.services import TranscriptService


# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------


def db_path_from_conn(conn) -> str:
    """Return the file path of the sqlite connection."""
    row = conn.execute("PRAGMA database_list").fetchone()
    return row[2] if row is not None else ":memory:"


# ---------------------------------------------------------------------------
# Durable output collection (daemon thread)
# ---------------------------------------------------------------------------


def collect_output_daemon(
    harness_run_id: str,
    proc: subprocess.Popen,
    db_path: str,
    sessions_dict: Dict[str, Dict[str, Any]],
) -> None:
    """Read stdout/stderr from ``proc`` line by line, persisting each
    line to ``harness_transcripts`` while the process runs.

    Uses its own sqlite connection so the Flask request thread can close
    its connection without breaking the daemon reader.

    Falls back to ``communicate()`` when the pipes aren't iterable
    (e.g. mocked Popen in tests).

    Parameters
    ----------
    harness_run_id:
        The HarnessRun to attach transcript lines to.
    proc:
        The subprocess whose stdout/stderr to read.
    db_path:
        Path to the SQLite database (for the background connection).
    sessions_dict:
        The adapter's in-memory ``{harness_run_id: info}`` dict.
        Updated with the final stdout/stderr after the process exits.
    """
    bg_conn = get_connection(db_path)
    bg_transcripts = TranscriptService()

    stdout_buf: list[str] = []
    stderr_buf: list[str] = []
    stdout_line_no = 0
    stderr_line_no = 0
    try:
        assert proc.stdout is not None and proc.stderr is not None
        try:
            first = proc.stdout.readline() if hasattr(proc.stdout, "readline") else None
            if first is not None and isinstance(first, str):
                stdout_line_no += 1
                stdout_buf.append(first)
                bg_transcripts.append(
                    bg_conn,
                    harness_run_id=harness_run_id,
                    stream="stdout",
                    content=first.rstrip("\n"),
                    line_no=stdout_line_no,
                )
                bg_conn.commit()
                for line in proc.stdout:
                    stdout_line_no += 1
                    stdout_buf.append(line)
                    bg_transcripts.append(
                        bg_conn,
                        harness_run_id=harness_run_id,
                        stream="stdout",
                        content=line.rstrip("\n"),
                        line_no=stdout_line_no,
                    )
                    bg_conn.commit()
                for line in proc.stderr:
                    stderr_line_no += 1
                    stderr_buf.append(line)
                    bg_transcripts.append(
                        bg_conn,
                        harness_run_id=harness_run_id,
                        stream="stderr",
                        content=line.rstrip("\n"),
                        line_no=stderr_line_no,
                    )
                    bg_conn.commit()
                rc = proc.wait()
            else:
                raise TypeError("pipe is not line-iterable")
        except TypeError:
            stdout, stderr = proc.communicate()
            if stdout:
                stdout_line_no = 1
                stdout_buf.append(stdout)
                bg_transcripts.append(
                    bg_conn,
                    harness_run_id=harness_run_id,
                    stream="stdout",
                    content=stdout.rstrip("\n") if isinstance(stdout, str) else str(stdout),
                    line_no=stdout_line_no,
                )
            if stderr:
                stderr_line_no = 1
                stderr_buf.append(stderr)
                bg_transcripts.append(
                    bg_conn,
                    harness_run_id=harness_run_id,
                    stream="stderr",
                    content=stderr.rstrip("\n") if isinstance(stderr, str) else str(stderr),
                    line_no=stderr_line_no,
                )
            rc = proc.returncode if proc.returncode is not None else 0
            bg_conn.commit()
    except Exception:
        bg_conn.close()
        return

    # Update in-memory mirror
    info = sessions_dict.get(harness_run_id)
    if info is not None:
        info["stdout"] = "".join(stdout_buf)
        info["stderr"] = "".join(stderr_buf)

    # Persist final exit metadata + status (guarded against
    # stop/cancel races via a conditional UPDATE).
    try:
        new_status = "completed" if rc == 0 else "failed"
        bg_conn.execute(
            """
            UPDATE harness_runs
            SET status = ?, ended_at = ?, exit_code = ?, exit_signal = ?
            WHERE harness_run_id = ?
              AND status NOT IN ('stopping', 'cancelled', 'completed', 'failed')
            """,
            (new_status, time.time(), rc, None, harness_run_id),
        )
        bg_transcripts.record_exit(
            bg_conn,
            harness_run_id=harness_run_id,
            returncode=rc,
            signal=None,
        )
        bg_conn.commit()
    except Exception:
        pass
    finally:
        bg_conn.close()


# ---------------------------------------------------------------------------
# Process group termination (stop / cancel)
# ---------------------------------------------------------------------------


def terminate_process_group(
    harness_run_id: str,
    sig: int,
    *,
    terminal_status: str,
    sessions_dict: Dict[str, Dict[str, Any]],
    repo: Any,
    transcripts: Any,
    conn: Any,
) -> None:
    """Send a signal to the process group for *harness_run_id*.

    Two paths:

    1. **In-memory** — the run is owned by this adapter instance
       (``sessions_dict`` has an entry).  We try ``os.killpg`` first,
       falling back to ``proc.terminate()`` / ``proc.kill()``.

    2. **Cross-request** — the run was started by a different adapter
       instance (or the server was restarted).  We read the persisted
       ``pgid`` / ``runtime_process_id`` from the database and signal
       directly.

    Parameters
    ----------
    sig:
        Signal number (e.g. ``signal.SIGTERM`` or ``signal.SIGKILL``).
    terminal_status:
        Status to set on the HarnessRun (e.g. ``"stopping"`` or ``"cancelled"``).
    sessions_dict:
        The adapter's in-memory ``{harness_run_id: info}`` dict.
    repo:
        A ``HarnessRunRepository`` instance.
    transcripts:
        A ``TranscriptService`` instance.
    conn:
        The sqlite3 connection (for event recording).
    """
    info = sessions_dict.get(harness_run_id)
    if info:
        proc = info["process"]
        if proc.poll() is not None:
            # Process already exited — set terminal status.
            terminal = "completed" if sig == signal.SIGTERM else "cancelled"
            repo.update_status(
                harness_run_id,
                status=terminal,
                ended_at=time.time(),
            )
            return
        pgid = info.get("pgid")
        try:
            if pgid:
                os.killpg(pgid, sig)
            else:
                if sig == signal.SIGTERM:
                    proc.terminate()
                else:
                    proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            if sig == signal.SIGTERM:
                try:
                    proc.terminate()
                except OSError:
                    pass
            else:
                try:
                    proc.kill()
                except OSError:
                    pass

        transcripts.append_event(
            conn,
            harness_run_id=harness_run_id,
            event_type="stop" if sig == signal.SIGTERM else "cancel",
            detail={"signal": sig, "pgid": pgid},
        )
        repo.update_status(
            harness_run_id,
            status=terminal_status,
            ended_at=time.time(),
        )
        return

    # Cross-request path: target the persisted pgid/pid directly.
    run = repo.get_by_id(harness_run_id)
    if run is None:
        from agent_workbench.adapters.base import HarnessNotReadyError
        raise HarnessNotReadyError(f"No process for {harness_run_id}")
    pgid = int(run.pgid) if run.pgid else None
    pid = int(run.runtime_process_id) if run.runtime_process_id else None
    if pgid is None and pid is None:
        from agent_workbench.adapters.base import HarnessNotReadyError
        raise HarnessNotReadyError(f"No process for {harness_run_id}")
    try:
        if pgid:
            os.killpg(pgid, sig)
        elif pid:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass

    transcripts.append_event(
        conn,
        harness_run_id=harness_run_id,
        event_type="stop" if sig == signal.SIGTERM else "cancel",
        detail={"signal": sig, "pgid": pgid, "pid": pid},
    )
    repo.update_status(
        harness_run_id,
        status=terminal_status,
        ended_at=time.time(),
    )
