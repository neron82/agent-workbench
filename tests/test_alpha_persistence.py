"""Tests for alpha persistence/domain lane — labels, users, assets, transfers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_workbench.db.migration_framework import apply_migrations
from agent_workbench.models.participant_transfer import (
    ParticipantTransferRepository,
)
from agent_workbench.models.project_asset import ProjectAssetRepository
from agent_workbench.models.session_label import SessionLabelRepository
from agent_workbench.models.user import UserRepository
from agent_workbench.services.asset_service import AssetService
from agent_workbench.services.identity_service import IdentityService
from agent_workbench.services.label_service import LabelService
from agent_workbench.services.participant_transfer_service import (
    ParticipantTransferService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _migrated_db(tmp_path: Path, seed_workspace: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "alpha.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    if seed_workspace:
        _seed_workspace(conn)
    return conn


def _seed_workspace(conn: sqlite3.Connection) -> str:
    ws_id = "ws-1"
    conn.execute(
        "INSERT OR IGNORE INTO workspaces (workspace_id, tenant_id, name, is_default, created_at) "
        "VALUES (?, 'default', 'Test Workspace', 1, 1.0)",
        (ws_id,),
    )
    conn.commit()
    return ws_id


def _seed_session(conn: sqlite3.Connection, ws_id: str, sid: str, stype: str = "chat") -> str:
    conn.execute(
        "INSERT OR IGNORE INTO session_extensions "
        "(session_id, workspace_id, session_type, status, created_at) "
        "VALUES (?, ?, ?, 'active', 1.0)",
        (sid, ws_id, stype),
    )
    conn.commit()
    return sid


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


class TestAlphaMigration:
    def test_011_applied(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        applied = {
            r["name"]
            for r in conn.execute("SELECT name FROM _migrations ORDER BY name").fetchall()
        }
        assert "011_alpha_persistence" in applied

    def test_tables_exist(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in ("session_labels", "users", "project_assets", "participant_transfers"):
            assert table in tables, f"Missing table: {table}"

    def test_builtin_labels_backfilled(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = LabelService(conn)
        labels = svc.list_labels(ws_id)
        names = {lb.name for lb in labels}
        assert names == {"chat", "research", "work"}

    def test_builtin_labels_are_builtin(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = LabelService(conn)
        for lb in svc.list_labels(ws_id):
            assert lb.is_builtin
            assert lb.display_name
            assert lb.color

    def test_migration_idempotent(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        import importlib
        mod = importlib.import_module("agent_workbench.db.migrations.011_alpha_persistence")
        # Run up again — should not error
        mod.up(conn)
        svc = LabelService(conn)
        labels = svc.list_labels(ws_id)
        assert len(labels) == 3  # no duplicates


    def test_orphan_session_channel_repair_is_idempotent(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        sid = _seed_session(conn, ws_id, "orphan-channel")
        conn.execute("DELETE FROM channels WHERE active_session_id = ?", (sid,))
        conn.commit()

        import importlib
        mod = importlib.import_module(
            "agent_workbench.db.migrations.012_repair_session_channels"
        )
        mod.up(conn)
        mod.up(conn)
        rows = conn.execute(
            "SELECT channel_id, active_session_id FROM channels "
            "WHERE active_session_id = ?",
            (sid,),
        ).fetchall()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Session Labels
# ---------------------------------------------------------------------------


class TestSessionLabelRepository:
    def test_create_and_get(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        repo = SessionLabelRepository(conn)
        label = repo.create(workspace_id=ws_id, name="debug", display_name="Debug", color="#FF0000")
        assert label.name == "debug"
        assert label.display_name == "Debug"
        assert label.color == "#FF0000"
        assert not label.is_builtin

        fetched = repo.get_by_id(label.label_id)
        assert fetched is not None
        assert fetched.name == "debug"

    def test_get_by_name(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = LabelService(conn)
        label = svc.get_label_by_name(ws_id, "chat")
        assert label is not None
        assert label.name == "chat"

    def test_get_by_name_missing(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        repo = SessionLabelRepository(conn)
        assert repo.get_by_name(ws_id, "nonexistent") is None

    def test_update_custom_label(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        repo = SessionLabelRepository(conn)
        label = repo.create(workspace_id=ws_id, name="custom", display_name="Custom")
        updated = repo.update(label.label_id, display_name="Updated", color="#00FF00")
        assert updated is not None
        assert updated.display_name == "Updated"
        assert updated.color == "#00FF00"

    def test_delete_custom_label(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        repo = SessionLabelRepository(conn)
        label = repo.create(workspace_id=ws_id, name="tmp", display_name="Temp")
        assert repo.delete(label.label_id)
        assert repo.get_by_id(label.label_id) is None

    def test_delete_builtin_blocked(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = LabelService(conn)
        builtin = svc.get_label_by_name(ws_id, "chat")
        assert builtin is not None
        # Repository-level delete rejects builtins
        repo = SessionLabelRepository(conn)
        assert not repo.delete(builtin.label_id)


class TestLabelService:
    def test_create_label(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = LabelService(conn)
        label = svc.create_label(workspace_id=ws_id, name="planning", display_name="Planning")
        assert label.name == "planning"
        assert not label.is_builtin

    def test_get_label_display_builtin(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = LabelService(conn)
        display = svc.get_label_display(ws_id, "chat")
        assert display["display_name"] == "Chat"
        assert display["color"] == "#4A90D9"
        assert display["is_builtin"]

    def test_get_label_display_fallback(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = LabelService(conn)
        display = svc.get_label_display(ws_id, "unknown_label")
        assert display["display_name"] == "Unknown_label"
        assert display["color"] == "#888888"
        assert not display["is_builtin"]

    def test_update_builtin_raises(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = LabelService(conn)
        label = svc.get_label_by_name(ws_id, "chat")
        assert label is not None
        with pytest.raises(ValueError, match="Builtin labels cannot be modified"):
            svc.update_label(label.label_id, display_name="Hacked")

    def test_delete_builtin_raises(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = LabelService(conn)
        label = svc.get_label_by_name(ws_id, "chat")
        assert label is not None
        with pytest.raises(ValueError, match="Builtin labels cannot be deleted"):
            svc.delete_label(label.label_id)


# ---------------------------------------------------------------------------
# Users / Identity
# ---------------------------------------------------------------------------


class TestUserRepository:
    def test_create_and_get(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        repo = UserRepository(conn)
        user = repo.create(display_name="Alice")
        assert user.display_name == "Alice"
        assert user.user_id

        fetched = repo.get_by_id(user.user_id)
        assert fetched is not None
        assert fetched.display_name == "Alice"

    def test_update_display_name(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        repo = UserRepository(conn)
        user = repo.create(display_name="Bob")
        updated = repo.update_display_name(user.user_id, "Robert")
        assert updated is not None
        assert updated.display_name == "Robert"

    def test_record_seen(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        repo = UserRepository(conn)
        user = repo.create(display_name="Test")
        seen = repo.record_seen(user.user_id)
        assert seen is not None
        assert seen.last_seen_at >= user.created_at


class TestIdentityService:
    def test_get_or_create_new(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        svc = IdentityService(conn)
        user = svc.get_or_create_user(display_name="Charlie")
        assert user.display_name == "Charlie"
        assert user.user_id

    def test_get_or_create_existing(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        svc = IdentityService(conn)
        user = svc.get_or_create_user(display_name="Diana")
        same = svc.get_or_create_user(user.user_id, display_name="Diana")
        assert same.user_id == user.user_id

    def test_get_or_create_updates_display_name(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        svc = IdentityService(conn)
        user = svc.get_or_create_user(display_name="Eve")
        updated = svc.get_or_create_user(user.user_id, display_name="Eve Newman")
        assert updated.display_name == "Eve Newman"

    def test_get_user_not_found(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        svc = IdentityService(conn)
        with pytest.raises(LookupError, match="User not found"):
            svc.get_user("nonexistent")


# ---------------------------------------------------------------------------
# Project Assets
# ---------------------------------------------------------------------------


class TestProjectAssetRepository:
    def test_create_and_get(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        repo = ProjectAssetRepository(conn)
        asset = repo.create(
            workspace_id=ws_id,
            asset_type="directory",
            path="src/components",
            label="Components",
        )
        assert asset.asset_type == "directory"
        assert asset.path == "src/components"
        assert not asset.session_id

        fetched = repo.get_by_id(asset.asset_id)
        assert fetched is not None
        assert fetched.path == "src/components"

    def test_list_by_workspace(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        repo = ProjectAssetRepository(conn)
        repo.create(workspace_id=ws_id, asset_type="file", path="readme.md", label="Readme")
        repo.create(workspace_id=ws_id, asset_type="directory", path="src", label="Source")
        assets = repo.list_by_workspace(ws_id)
        assert len(assets) == 2

    def test_list_by_type(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        repo = ProjectAssetRepository(conn)
        repo.create(workspace_id=ws_id, asset_type="file", path="a.txt", label="A")
        repo.create(workspace_id=ws_id, asset_type="directory", path="dir", label="Dir")
        files = repo.list_by_workspace(ws_id, asset_type="file")
        assert len(files) == 1
        assert files[0].asset_type == "file"

    def test_list_by_session(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        sid = _seed_session(conn, ws_id, "sess-1")
        repo = ProjectAssetRepository(conn)
        repo.create(workspace_id=ws_id, asset_type="file", path="data.csv", label="Data", session_id=sid)
        assets = repo.list_by_session(sid)
        assert len(assets) == 1
        assert assets[0].session_id == sid

    def test_delete(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        repo = ProjectAssetRepository(conn)
        asset = repo.create(workspace_id=ws_id, asset_type="file", path="old.txt", label="Old")
        assert repo.delete(asset.asset_id)
        assert repo.get_by_id(asset.asset_id) is None


class TestAssetService:
    def test_add_asset(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = AssetService(conn)
        asset = svc.add_asset(
            workspace_id=ws_id, asset_type="directory", path="src/lib", label="Library"
        )
        assert asset.path == "src/lib"
        assert asset.label == "Library"

    def test_allows_absolute_path(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = AssetService(conn)
        root = tmp_path / "repo"
        root.mkdir()
        asset = svc.add_asset(
            workspace_id=ws_id, asset_type="directory", path=str(root), label="Repo"
        )
        assert asset.path == str(root)

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = AssetService(conn)
        with pytest.raises(ValueError, match="Path traversal"):
            svc.add_asset(workspace_id=ws_id, asset_type="file", path="safe/../../etc/passwd")

    def test_rejects_simple_traversal(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = AssetService(conn)
        with pytest.raises(ValueError, match="Path traversal"):
            svc.add_asset(workspace_id=ws_id, asset_type="file", path="../etc/passwd")

    def test_rejects_empty_path(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = AssetService(conn)
        with pytest.raises(ValueError, match="must not be empty"):
            svc.add_asset(workspace_id=ws_id, asset_type="file", path="")

    def test_allows_relative_path_with_dots(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = AssetService(conn)
        asset = svc.add_asset(
            workspace_id=ws_id, asset_type="file", path="some.dir/file.txt", label="File"
        )
        assert asset.path == "some.dir/file.txt"

    def test_list_assets(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = AssetService(conn)
        svc.add_asset(workspace_id=ws_id, asset_type="directory", path="docs", label="Docs")
        svc.add_asset(workspace_id=ws_id, asset_type="file", path="notes.txt", label="Notes")
        assets = svc.list_assets(ws_id)
        assert len(assets) == 2

    def test_remove_asset(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = AssetService(conn)
        asset = svc.add_asset(workspace_id=ws_id, asset_type="file", path="tmp.txt", label="Tmp")
        svc.remove_asset(asset.asset_id)
        with pytest.raises(LookupError):
            svc.get_asset(asset.asset_id)


# ---------------------------------------------------------------------------
# Participant Transfers
# ---------------------------------------------------------------------------


class TestParticipantTransferRepository:
    def test_create_and_get(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        src = _seed_session(conn, ws_id, "src-1")
        tgt = _seed_session(conn, ws_id, "tgt-1")
        repo = ParticipantTransferRepository(conn)
        transfer = repo.create(
            source_session_id=src,
            target_session_id=tgt,
            context_summary="Moving context",
            transferred_participants=[{"agent_id": "a1", "name": "Agent A"}],
        )
        assert transfer.source_session_id == src
        assert transfer.target_session_id == tgt
        assert transfer.status == "pending"
        assert len(transfer.transferred_participants) == 1

        fetched = repo.get_by_id(transfer.transfer_id)
        assert fetched is not None
        assert fetched.context_summary == "Moving context"

    def test_list_by_source(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        src = _seed_session(conn, ws_id, "src-list")
        tgt = _seed_session(conn, ws_id, "tgt-list")
        repo = ParticipantTransferRepository(conn)
        repo.create(source_session_id=src, target_session_id=tgt)
        results = repo.list_by_source(src)
        assert len(results) == 1

    def test_list_by_target(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        src = _seed_session(conn, ws_id, "src-tgt")
        tgt = _seed_session(conn, ws_id, "tgt-tgt")
        repo = ParticipantTransferRepository(conn)
        repo.create(source_session_id=src, target_session_id=tgt)
        results = repo.list_by_target(tgt)
        assert len(results) == 1

    def test_update_status(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        src = _seed_session(conn, ws_id, "src-st")
        tgt = _seed_session(conn, ws_id, "tgt-st")
        repo = ParticipantTransferRepository(conn)
        transfer = repo.create(source_session_id=src, target_session_id=tgt)
        completed = repo.update_status(transfer.transfer_id, status="completed")
        assert completed is not None
        assert completed.status == "completed"
        assert completed.completed_at is not None


class TestParticipantTransferService:
    def test_create_transfer(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        src = _seed_session(conn, ws_id, "svc-src")
        tgt = _seed_session(conn, ws_id, "svc-tgt")
        svc = ParticipantTransferService(conn)
        transfer = svc.create_transfer(
            source_session_id=src,
            target_session_id=tgt,
            context_summary="Transferring work context",
        )
        assert transfer.status == "pending"
        assert transfer.context_summary == "Transferring work context"

    def test_transfer_to_new_session_creates_valid_fork_link(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        src = _seed_session(conn, ws_id, "continuation-src")
        svc = ParticipantTransferService(conn)

        target, transfer = svc.transfer_to_new_session(
            source_session_id=src,
            context_summary="Continue the discussion",
            initiated_by="user",
        )

        assert target.workspace_id == ws_id
        assert target.fork_id
        assert transfer.status == "completed"
        fork = conn.execute(
            "SELECT fork_id, parent_session_id, child_session_id "
            "FROM fork_records WHERE fork_id = ?",
            (target.fork_id,),
        ).fetchone()
        assert fork is not None
        assert fork["parent_session_id"] == src
        assert fork["child_session_id"] == target.session_id
        channel = conn.execute(
            "SELECT channel_kind, active_session_id FROM channels "
            "WHERE active_session_id = ?",
            (target.session_id,),
        ).fetchone()
        assert channel is not None
        assert channel["channel_kind"] == "chat"

    def test_create_transfer_source_not_found(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        tgt = _seed_session(conn, ws_id, "tgt-only")
        svc = ParticipantTransferService(conn)
        with pytest.raises(LookupError, match="Source session not found"):
            svc.create_transfer(source_session_id="nonexistent", target_session_id=tgt)

    def test_complete_transfer(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        src = _seed_session(conn, ws_id, "comp-src")
        tgt = _seed_session(conn, ws_id, "comp-tgt")
        svc = ParticipantTransferService(conn)
        transfer = svc.create_transfer(source_session_id=src, target_session_id=tgt)
        completed = svc.complete_transfer(transfer.transfer_id)
        assert completed.status == "completed"

    def test_fail_transfer(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        src = _seed_session(conn, ws_id, "fail-src")
        tgt = _seed_session(conn, ws_id, "fail-tgt")
        svc = ParticipantTransferService(conn)
        transfer = svc.create_transfer(source_session_id=src, target_session_id=tgt)
        failed = svc.fail_transfer(transfer.transfer_id)
        assert failed.status == "failed"

    def test_cancel_transfer(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        src = _seed_session(conn, ws_id, "cancel-src")
        tgt = _seed_session(conn, ws_id, "cancel-tgt")
        svc = ParticipantTransferService(conn)
        transfer = svc.create_transfer(source_session_id=src, target_session_id=tgt)
        cancelled = svc.cancel_transfer(transfer.transfer_id)
        assert cancelled.status == "cancelled"

    def test_cannot_complete_non_pending(self, tmp_path: Path) -> None:
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        src = _seed_session(conn, ws_id, "np-src")
        tgt = _seed_session(conn, ws_id, "np-tgt")
        svc = ParticipantTransferService(conn)
        transfer = svc.create_transfer(source_session_id=src, target_session_id=tgt)
        svc.complete_transfer(transfer.transfer_id)
        with pytest.raises(ValueError, match="Cannot complete"):
            svc.complete_transfer(transfer.transfer_id)


# ---------------------------------------------------------------------------
# Session Extension — label compatibility (existing session_type + labels)
# ---------------------------------------------------------------------------


class TestLabelBackwardCompatibility:
    def test_session_type_remains_compatible_field(self, tmp_path: Path) -> None:
        """Existing session_type values still work after migration."""
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        sid = _seed_session(conn, ws_id, "compat-sess", stype="research")
        row = conn.execute(
            "SELECT session_type FROM session_extensions WHERE session_id = ?",
            (sid,),
        ).fetchone()
        assert row is not None
        assert row["session_type"] == "research"

    def test_backfilled_labels_correspond_to_types(self, tmp_path: Path) -> None:
        """Backfilled labels match the known session_type values."""
        conn = _migrated_db(tmp_path)
        ws_id = _seed_workspace(conn)
        svc = LabelService(conn)
        labels = svc.list_labels(ws_id)
        label_names = {lb.name for lb in labels}
        assert label_names == {"chat", "research", "work"}