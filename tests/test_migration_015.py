"""Tests for migration 015 — repair event_records FK targeting routed_messages_old.

Verifies:
1. Fresh DB through 014: 015 is a no-op (FK already correct).
2. Damaged DB (event_records FK → routed_messages_old): 015 repairs it,
   preserves rows, leaves PRAGMA foreign_keys ON.
3. Idempotent reapply: second run does nothing.
4. After repair, a workspace DELETE that previously failed now succeeds.
5. Unrelated FK violations (session_participants → agent_profile_bindings)
   are NOT silently cleaned.
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.web import create_app


class TestMigration015RepairEventRecordsFK:
    """Focused tests for migration 015."""

    def _migrate_through_014(self, db_path: Path) -> sqlite3.Connection:
        """Create a fresh DB and apply all migrations, then remove 015
        from the tracking table so we can test it separately."""
        conn = get_connection(str(db_path))
        apply_migrations(conn)
        # Remove 015 from _migrations so it can be re-applied in tests
        conn.execute(
            "DELETE FROM _migrations WHERE name = '015_repair_event_records_routed_message_fk'"
        )
        conn.commit()
        # Verify 014 is applied but 015 is not
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM _migrations"
        ).fetchall()}
        assert "014_confirmation_context" in names
        assert "015_repair_event_records_routed_message_fk" not in names
        return conn

    def _damage_event_records_fk(self, conn: sqlite3.Connection) -> None:
        """Replace event_records with a version referencing routed_messages_old."""
        # Save rows
        rows = conn.execute(
            "SELECT event_id, harness_run_id, routed_message_id, "
            "event_type, event_source, event_payload_ref, event_ts "
            "FROM event_records"
        ).fetchall()

        conn.commit()  # ensure outside transaction for PRAGMA
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.commit()
        conn.execute("DROP TABLE event_records")
        conn.execute(
            """\
            CREATE TABLE event_records (
                event_id            TEXT PRIMARY KEY,
                harness_run_id      TEXT REFERENCES harness_runs(harness_run_id),
                routed_message_id   TEXT REFERENCES routed_messages_old(routed_message_id),
                event_type          TEXT NOT NULL,
                event_source        TEXT NOT NULL,
                event_payload_ref   TEXT,
                event_ts            REAL NOT NULL
            )
            """
        )
        for row in rows:
            conn.execute(
                "INSERT INTO event_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["event_id"], row["harness_run_id"], row["routed_message_id"],
                 row["event_type"], row["event_source"], row["event_payload_ref"],
                 row["event_ts"]),
            )
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        # Verify the damage
        fk_list = conn.execute("PRAGMA foreign_key_list(event_records)").fetchall()
        assert any(r["table"] == "routed_messages_old" for r in fk_list)

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_015_noop_on_healthy_schema(self, tmp_path: Path) -> None:
        """015 does nothing when event_records already targets routed_messages."""
        conn = self._migrate_through_014(tmp_path / "healthy.db")
        try:
            # Verify FK is correct before 015
            fk_before = conn.execute(
                "PRAGMA foreign_key_list(event_records)"
            ).fetchall()
            assert not any(r["table"] == "routed_messages_old" for r in fk_before)

            # Apply 015
            applied = apply_migrations(conn)
            assert "015_repair_event_records_routed_message_fk" in applied

            # FK still correct
            fk_after = conn.execute(
                "PRAGMA foreign_key_list(event_records)"
            ).fetchall()
            assert not any(r["table"] == "routed_messages_old" for r in fk_after)

            # PRAGMA foreign_keys is ON
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            conn.close()

    def test_015_repairs_damaged_fk_and_preserves_rows(
        self, tmp_path: Path
    ) -> None:
        """015 repairs event_records FK and preserves all rows."""
        db_path = tmp_path / "damaged.db"
        conn = self._migrate_through_014(db_path)

        # Seed some data so we have rows to preserve
        ws = WorkspaceRepository(conn).create(
            tenant_id="t1", name="Repair Test", is_default=True
        )
        conn.execute(
            "INSERT INTO session_extensions "
            "(session_id, workspace_id, session_type) "
            "VALUES (?, ?, 'chat')",
            ("test-session-1", ws.workspace_id),
        )
        conn.execute(
            "INSERT INTO harness_runs "
            "(harness_run_id, workspace_id, session_id, harness_type) "
            "VALUES (?, ?, ?, 'hermes')",
            ("test-hrun-1", ws.workspace_id, "test-session-1"),
        )
        conn.execute(
            "INSERT INTO routed_messages "
            "(routed_message_id, workspace_id, session_id, channel_id, "
            "source_type, source_id, target_type, target_id, message_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'conversation')",
            ("test-rm-1", ws.workspace_id, "test-session-1",
             "test-channel-1", "user", "u1", "agent", "a1"),
        )
        conn.execute(
            "INSERT INTO event_records "
            "(event_id, harness_run_id, routed_message_id, "
            "event_type, event_source, event_ts) "
            "VALUES (?, ?, ?, ?, ?, 1000.0)",
            ("test-ev-1", "test-hrun-1", "test-rm-1",
             "test_type", "test_source"),
        )
        conn.commit()

        # Count rows before damage
        count_before = conn.execute(
            "SELECT COUNT(*) FROM event_records"
        ).fetchone()[0]
        assert count_before == 1

        # Damage the FK
        self._damage_event_records_fk(conn)

        # Verify damage: FK targets routed_messages_old
        fk_damaged = conn.execute(
            "PRAGMA foreign_key_list(event_records)"
        ).fetchall()
        assert any(r["table"] == "routed_messages_old" for r in fk_damaged)

        # Apply pending migrations (015)
        applied = apply_migrations(conn)
        assert "015_repair_event_records_routed_message_fk" in applied

        # Rows preserved
        count_after = conn.execute(
            "SELECT COUNT(*) FROM event_records"
        ).fetchone()[0]
        assert count_after == count_before

        # FK now targets routed_messages
        fk_repaired = conn.execute(
            "PRAGMA foreign_key_list(event_records)"
        ).fetchall()
        assert not any(r["table"] == "routed_messages_old" for r in fk_repaired)
        assert any(r["table"] == "routed_messages" for r in fk_repaired)

        # PRAGMA foreign_keys is ON
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        # Data integrity: the row is still readable
        row = conn.execute(
            "SELECT * FROM event_records WHERE event_id = ?",
            ("test-ev-1",),
        ).fetchone()
        assert row is not None
        assert row["routed_message_id"] == "test-rm-1"
        conn.close()

    def test_015_idempotent_reapply(self, tmp_path: Path) -> None:
        """Reapplying 015 does nothing."""
        db_path = tmp_path / "idem.db"
        conn = self._migrate_through_014(db_path)

        # Damage
        self._damage_event_records_fk(conn)

        # First apply
        first = apply_migrations(conn)
        assert "015_repair_event_records_routed_message_fk" in first

        # Second apply — 015 should NOT be applied again
        second = apply_migrations(conn)
        assert "015_repair_event_records_routed_message_fk" not in second
        assert len(second) == 0

        # FK still correct
        fk = conn.execute("PRAGMA foreign_key_list(event_records)").fetchall()
        assert not any(r["table"] == "routed_messages_old" for r in fk)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.close()

    def test_015_recovers_when_stale_old_table_exists(self, tmp_path: Path) -> None:
        """A stale temp table from an interrupted attempt cannot strand retries."""
        conn = self._migrate_through_014(tmp_path / "stale-old.db")
        self._damage_event_records_fk(conn)
        conn.execute("CREATE TABLE event_records_old (event_id TEXT PRIMARY KEY)")
        conn.commit()

        applied = apply_migrations(conn)

        assert "015_repair_event_records_routed_message_fk" in applied
        assert conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='event_records_old'"
        ).fetchone() is None
        targets = {
            row["table"]
            for row in conn.execute("PRAGMA foreign_key_list(event_records)")
        }
        assert "routed_messages" in targets
        assert "routed_messages_old" not in targets
        conn.close()

    def test_015_rolls_back_all_ddl_on_mid_copy_failure(self, tmp_path: Path) -> None:
        """An explicit transaction preserves the original schema on failure."""
        conn = self._migrate_through_014(tmp_path / "ddl-rollback.db")
        self._damage_event_records_fk(conn)
        migration = importlib.import_module(
            "agent_workbench.db.migrations."
            "015_repair_event_records_routed_message_fk"
        )

        def deny_event_copy(action, arg1, _arg2, _db_name, _trigger_name):
            if action == sqlite3.SQLITE_INSERT and arg1 == "event_records":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(deny_event_copy)
        try:
            with pytest.raises(sqlite3.DatabaseError):
                migration.up(conn)
        finally:
            conn.set_authorizer(None)

        assert conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='event_records_old'"
        ).fetchone() is None
        targets = {
            row["table"]
            for row in conn.execute("PRAGMA foreign_key_list(event_records)")
        }
        assert "routed_messages_old" in targets
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.close()

    def test_015_does_not_touch_unrelated_fk_violations(
        self, tmp_path: Path
    ) -> None:
        """Unrelated FK violations (e.g. session_participants) are left alone."""
        db_path = tmp_path / "unrelated.db"
        conn = self._migrate_through_014(db_path)

        # Create an orphan session_participant (FK violation to
        # agent_profile_bindings) — this is the known unrelated violation.
        ws = WorkspaceRepository(conn).create(
            tenant_id="t1", name="Unrelated FK", is_default=True
        )
        conn.execute(
            "INSERT INTO session_extensions "
            "(session_id, workspace_id, session_type) "
            "VALUES (?, ?, 'chat')",
            ("orphan-session", ws.workspace_id),
        )
        # Insert a session_participant with a bogus binding_id
        conn.commit()  # ensure we're outside a transaction for PRAGMA
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.commit()
        conn.execute(
            "INSERT INTO session_participants "
            "(participant_id, session_id, workspace_id, binding_id, added_by) "
            "VALUES (?, ?, ?, ?, 'user')",
            ("orphan-participant", "orphan-session", ws.workspace_id, "nonexistent-binding"),
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        # Damage event_records FK
        self._damage_event_records_fk(conn)

        # Apply 015
        applied = apply_migrations(conn)
        assert "015_repair_event_records_routed_message_fk" in applied

        # The orphan session_participant should still exist
        orphan = conn.execute(
            "SELECT COUNT(*) FROM session_participants "
            "WHERE participant_id = 'orphan-participant'"
        ).fetchone()[0]
        assert orphan == 1, "015 must not clean unrelated FK violations"

        conn.close()

    def test_015_enables_workspace_delete_after_repair(
        self, tmp_path: Path
    ) -> None:
        """After 015 repair, a workspace POST delete that previously failed
        due to the stale FK now succeeds."""
        db_path = tmp_path / "delete-repair.db"
        conn = self._migrate_through_014(db_path)

        # Create a workspace with a session (so there's data)
        ws = WorkspaceRepository(conn).create(
            tenant_id="t1", name="Delete Me", is_default=True
        )
        conn.execute(
            "INSERT INTO session_extensions "
            "(session_id, workspace_id, session_type) "
            "VALUES (?, ?, 'chat')",
            ("del-session", ws.workspace_id),
        )
        conn.execute(
            "INSERT INTO channels "
            "(channel_id, workspace_id, channel_kind, title, active_session_id) "
            "VALUES (?, ?, 'chat', 'del-channel', 'del-session')",
            ("del-channel", ws.workspace_id),
        )
        conn.execute(
            "INSERT INTO harness_runs "
            "(harness_run_id, workspace_id, session_id, harness_type) "
            "VALUES (?, ?, ?, 'hermes')",
            ("del-hrun", ws.workspace_id, "del-session"),
        )
        conn.execute(
            "INSERT INTO routed_messages "
            "(routed_message_id, workspace_id, session_id, channel_id, "
            "source_type, source_id, target_type, target_id, message_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'conversation')",
            ("del-rm", ws.workspace_id, "del-session",
             "del-channel", "user", "u1", "agent", "a1"),
        )
        conn.execute(
            "INSERT INTO event_records "
            "(event_id, harness_run_id, routed_message_id, "
            "event_type, event_source, event_ts) "
            "VALUES (?, ?, ?, ?, ?, 1000.0)",
            ("del-ev", "del-hrun", "del-rm", "type", "src"),
        )
        conn.commit()

        # Damage event_records FK
        self._damage_event_records_fk(conn)

        # Verify that before 015, a direct DELETE fails due to stale FK
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "DELETE FROM event_records WHERE harness_run_id = ?",
                ("del-hrun",),
            )
        conn.close()

        # Now apply 015 via the framework
        conn = get_connection(str(db_path))
        apply_migrations(conn)
        conn.close()

        # Create app + client (after repair)
        app = create_app(db_path=str(db_path))
        app.config.update(TESTING=True, WORKBENCH_AGENT_RESPONSE_MODE="sync")
        from tests.conftest import make_csrf_client
        client = make_csrf_client(app)

        # Delete the session first (so workspace is empty)
        resp = client.post(
            "/sessions/del-session/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Also delete the channel (session delete NULLs active_session_id
        # but doesn't remove the channel itself)
        conn = get_connection(str(db_path))
        conn.execute("DELETE FROM channels WHERE workspace_id = ?", (ws.workspace_id,))
        conn.commit()
        conn.close()

        # Now delete the workspace
        resp = client.post(
            f"/workspaces/{ws.workspace_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Verify workspace is gone
        conn = get_connection(str(db_path))
        assert WorkspaceRepository(conn).get_by_id(ws.workspace_id) is None
        conn.close()

    def test_015_noop_when_event_records_absent(self, tmp_path: Path) -> None:
        """015 does nothing when event_records table does not exist."""
        db_path = tmp_path / "no-events.db"
        conn = self._migrate_through_014(db_path)

        # Drop event_records
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE event_records")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        # Apply 015 — should be a no-op
        applied = apply_migrations(conn)
        assert "015_repair_event_records_routed_message_fk" in applied

        # Table still absent
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='event_records'"
        ).fetchone()
        assert row is None

        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.close()
