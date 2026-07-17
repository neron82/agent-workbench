"""Tests for the persistent transcript service (migration 003)."""

from __future__ import annotations

import time

import pytest

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.services import TranscriptService


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test.db"
    c = get_connection(str(db))
    apply_migrations(c)
    return c


def _make_workspace_and_run(conn):
    ws = WorkspaceRepository(conn).create(tenant_id="t1", name="t")
    conn.commit()
    hr = HarnessRunRepository(conn).create(
        workspace_id=ws.workspace_id,
        session_id="s1",
        harness_type="shell",
        status="running",
    )
    conn.commit()
    return hr.harness_run_id


class TestTranscriptAppend:
    def test_append_returns_uuid(self, conn):
        rid = _make_workspace_and_run(conn)
        svc = TranscriptService()
        tid = svc.append(conn, harness_run_id=rid, stream="stdout", content="hello")
        assert isinstance(tid, str) and len(tid) == 36
        conn.commit()
        assert svc.count(conn, harness_run_id=rid) == 1

    def test_append_rejects_invalid_stream(self, conn):
        rid = _make_workspace_and_run(conn)
        with pytest.raises(ValueError):
            TranscriptService().append(
                conn, harness_run_id=rid, stream="bogus", content="x"
            )

    def test_append_lines_are_ordered_by_captured_at(self, conn):
        rid = _make_workspace_and_run(conn)
        svc = TranscriptService()
        for i in range(5):
            svc.append(
                conn,
                harness_run_id=rid,
                stream="stdout",
                content=f"line {i}",
                line_no=i + 1,
            )
            time.sleep(0.01)
        conn.commit()
        rows = svc.list(conn, harness_run_id=rid)
        assert [r["content"] for r in rows] == [
            "line 0", "line 1", "line 2", "line 3", "line 4",
        ]


class TestEvents:
    def test_append_and_list_events(self, conn):
        rid = _make_workspace_and_run(conn)
        svc = TranscriptService()
        svc.append_event(
            conn,
            harness_run_id=rid,
            event_type="start",
            detail={"pid": 1234, "pgid": 1234},
        )
        svc.append_event(
            conn,
            harness_run_id=rid,
            event_type="status_change",
            detail={"status": "running"},
        )
        conn.commit()
        events = svc.list_events(conn, harness_run_id=rid)
        assert [e["event_type"] for e in events] == ["start", "status_change"]
        assert events[0]["detail"]["pid"] == 1234

    def test_append_event_rejects_invalid_type(self, conn):
        rid = _make_workspace_and_run(conn)
        with pytest.raises(ValueError):
            TranscriptService().append_event(
                conn,
                harness_run_id=rid,
                event_type="not-a-real-type",
            )


class TestRecordExit:
    def test_record_exit_writes_code_and_signal(self, conn):
        rid = _make_workspace_and_run(conn)
        svc = TranscriptService()
        svc.record_exit(conn, harness_run_id=rid, returncode=0, signal=None)
        conn.commit()
        hr = HarnessRunRepository(conn).get_by_id(rid)
        assert hr.exit_code == 0
        assert hr.exit_signal is None
        events = svc.list_events(conn, harness_run_id=rid)
        assert any(e["event_type"] == "exit" for e in events)
