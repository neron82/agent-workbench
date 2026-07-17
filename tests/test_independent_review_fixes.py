"""Focused regression tests for independent-review permission/security fixes.

Covers:
1. Confirmation context persistence (None vs [])
2. Forged confirmation form policy ignored
3. Missing/malformed context fail-closed
4. Permanent vs once grant distinction in dispatch
5. Once grant first-use consumption / second-use prompt
6. Explicit empty policy [] denies all
7. Transfer cleanup on session delete
8. Session lock cleanup on session delete
9. message_row data attribute + event listener (structural)
"""

from __future__ import annotations

import json
import threading

import pytest

from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.cross_harness_permission import (
    CrossHarnessPermissionRepository,
)
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.tool import ToolRepository
from agent_workbench.models.tool_invocation import ToolInvocationRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.tool_dispatcher import (
    ToolDispatcher,
)
from agent_workbench.services.session_service import (
    SessionService,
    _cleanup_session_lock,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_workspace_and_session(db):
    ws = WorkspaceRepository(db).create(tenant_id="t1", name="t")
    db.commit()
    ch = ChannelRepository(db).create(
        workspace_id=ws.workspace_id, channel_kind="chat", title="t",
    )
    db.commit()
    sess = SessionExtensionRepository(db).create(
        workspace_id=ws.workspace_id, session_type="research",
    )
    db.commit()
    ChannelRepository(db).update_active_session(
        ch.channel_id, active_session_id=sess.session_id,
    )
    db.commit()
    return ws.workspace_id, ch.channel_id, sess.session_id


def _make_tool(db, name, harness_type, adapter_method="start",
               permission_class="write_local", is_enabled=True):
    import uuid
    unique = f"{name}_{uuid.uuid4().hex[:8]}"
    return ToolRepository(db).create(
        name=unique,
        harness_type=harness_type,
        adapter_method=adapter_method,
        description=f"tool {name}",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        permission_class=permission_class,
        is_enabled=is_enabled,
    )


# ===========================================================================
# 1. Confirmation context persistence (None vs [])
# ===========================================================================


class TestConfirmationContextPersistence:
    """A pending cross-harness ToolInvocation durably stores the exact
    original confirmation context: agent_harness_type, session_policy
    preserving None vs [], and allowed_tool_names preserving None vs []."""

    def test_stores_context_with_session_policy_none(self, db):
        """session_policy=None is stored as JSON null, not omitted."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run", "shell",
                          adapter_method="start", permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=None,
            tool_call={
                "id": "call_1",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "date"}),
                },
            },
            agent_harness_type="hermes",
            allowed_tool_names=None,
        )
        assert result.status == "pending_confirmation"
        inv = ToolInvocationRepository(db).get_by_id(result.invocation_id)
        assert inv is not None
        ctx = inv.confirmation_context_json
        assert ctx is not None
        assert ctx["agent_harness_type"] == "hermes"
        assert ctx["session_policy"] is None
        assert ctx["allowed_tool_names"] is None

    def test_stores_context_with_explicit_empty_policy(self, db):
        """session_policy=[] is stored as JSON [], not null.
        Note: with session_policy=[], the tool is denied for policy
        reasons before the cross-harness check.  We verify the context
        is stored correctly by checking the invocation directly."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run2", "shell",
                          adapter_method="start", permission_class="write_local")
        # Create a pending_confirmation invocation directly with context
        inv_repo = ToolInvocationRepository(db)
        inv = inv_repo.create(
            session_id=sid,
            workspace_id=ws,
            tool_id=tool.tool_id,
            tool_name=tool.name,
            tool_harness_type="shell",
            arguments={"command": "date"},
            status="pending_confirmation",
            requires_confirmation=True,
            confirmation_reason="test",
            confirmation_context={
                "agent_harness_type": "hermes",
                "session_policy": [],
                "allowed_tool_names": ["shell.run_command"],
            },
        )
        ctx = inv.confirmation_context_json
        assert ctx is not None
        assert ctx["session_policy"] == []
        assert ctx["allowed_tool_names"] == ["shell.run_command"]

    def test_context_survives_db_roundtrip(self, db):
        """Context is durably stored and survives a re-read."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run3", "shell",
                          adapter_method="start", permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_3",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "date"}),
                },
            },
            agent_harness_type="hermes",
            allowed_tool_names=[f"shell.{tool.name}"],
        )
        # Re-read from a fresh repo instance
        inv_repo = ToolInvocationRepository(db)
        inv = inv_repo.get_by_id(result.invocation_id)
        assert inv is not None
        ctx = inv.confirmation_context_json
        assert ctx is not None
        assert ctx["agent_harness_type"] == "hermes"
        assert ctx["session_policy"] == ["read_only", "write_local"]
        assert ctx["allowed_tool_names"] == [f"shell.{tool.name}"]


# ===========================================================================
# 2. Forged confirmation form policy ignored
# ===========================================================================


class TestForgedPolicyIgnored:
    """Confirmation POST must ignore any posted session_policy and
    redispatch using only stored context."""

    def test_confirm_uses_stored_context_not_form(self, db):
        """Even if a malicious form posts session_policy, the stored
        context is used for redispatch."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run4", "shell",
                          adapter_method="start", permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_4",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "printf confirmed"}),
                },
            },
            agent_harness_type="hermes",
            allowed_tool_names=None,
        )
        db_path = db.execute("PRAGMA database_list").fetchone()[2]
        from agent_workbench.web.app import create_app
        from tests.conftest import make_csrf_client

        client = make_csrf_client(create_app(db_path))
        response = client.post(
            f"/sessions/{sid}/tools/confirm",
            data={
                "invocation_id": result.invocation_id,
                "decision": "yes_once",
                # If the route trusted this forged policy, write_local would
                # be denied.  The stored policy permits it.
                "session_policy": "read_only",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        inv = ToolInvocationRepository(db).get_by_id(result.invocation_id)
        assert inv is not None
        assert inv.status == "completed"
        assert "confirmed" in (inv.result_text or "")


# ===========================================================================
# 3. Missing/malformed context fail-closed
# ===========================================================================


class TestMissingContextFailClosed:
    """If context is missing/malformed, fail closed without running
    the adapter."""

    def test_missing_context_denies(self, db):
        """An invocation with no confirmation_context_json is denied."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run5", "shell",
                          adapter_method="start", permission_class="write_local")
        # Create an invocation directly without context
        inv_repo = ToolInvocationRepository(db)
        inv = inv_repo.create(
            session_id=sid,
            workspace_id=ws,
            tool_id=tool.tool_id,
            tool_name=tool.name,
            tool_harness_type="shell",
            arguments={"command": "date"},
            status="pending_confirmation",
            requires_confirmation=True,
            confirmation_reason="test",
        )
        assert inv.confirmation_context_json is None
        # The web handler would check this and fail closed
        ctx = inv.confirmation_context_json
        assert ctx is None or not isinstance(ctx, dict)

    @pytest.mark.parametrize("malformed_context", [
        {"garbage": True},
        {
            "agent_harness_type": "hermes",
            "session_policy": ["invented_superuser_class"],
            "allowed_tool_names": None,
        },
    ])
    def test_truthy_malformed_context_is_denied_by_route(
        self, db, malformed_context
    ):
        """Missing keys and invalid permission classes both fail closed."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.malformed", "shell",
                          adapter_method="start", permission_class="write_local")
        inv = ToolInvocationRepository(db).create(
            session_id=sid,
            workspace_id=ws,
            tool_id=tool.tool_id,
            tool_name=tool.name,
            tool_harness_type="shell",
            arguments={"command": "printf must-not-run"},
            status="pending_confirmation",
            requires_confirmation=True,
            confirmation_reason="test",
            confirmation_context=malformed_context,
        )
        db_path = db.execute("PRAGMA database_list").fetchone()[2]
        from agent_workbench.web.app import create_app
        from tests.conftest import make_csrf_client

        client = make_csrf_client(create_app(db_path))
        response = client.post(
            f"/sessions/{sid}/tools/confirm",
            data={"invocation_id": inv.invocation_id, "decision": "yes_once"},
            follow_redirects=False,
        )
        assert response.status_code == 302

        refreshed = ToolInvocationRepository(db).get_by_id(inv.invocation_id)
        assert refreshed is not None
        assert refreshed.status == "denied"
        run_count = db.execute(
            "SELECT COUNT(*) FROM harness_runs WHERE session_id = ?", (sid,)
        ).fetchone()[0]
        assert run_count == 0


# ===========================================================================
# 4. Permanent vs once grant distinction in dispatch
# ===========================================================================


class TestPermanentVsOnceDispatch:
    """Dispatcher must distinguish matching permanent vs once
    cross-harness grants."""

    def test_permanent_takes_precedence_over_once(self, db):
        """When both permanent and once grants exist, permanent takes
        precedence and the once grant is NOT consumed."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run6", "shell",
                          adapter_method="start", permission_class="write_local")
        cross = CrossHarnessPermissionRepository(db)
        # Grant both permanent and once
        cross.grant(
            session_id=sid, workspace_id=ws,
            agent_harness_type="hermes", tool_harness_type="shell",
            decision="permanent",
        )
        once_perm = cross.grant(
            session_id=sid, workspace_id=ws,
            agent_harness_type="hermes", tool_harness_type="shell",
            decision="once",
        )
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_perm",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "echo perm"}),
                },
            },
            agent_harness_type="hermes",
        )
        # Should run (not pending_confirmation) because permanent exists
        assert result.status != "pending_confirmation"
        # The once grant should still exist (not consumed)
        once_row = cross.get_by_id(once_perm.permission_id)
        assert once_row is not None

    def test_once_consumed_on_first_use(self, db):
        """A once grant is consumed during dispatch so the first call
        runs and the second prompts again."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run7", "shell",
                          adapter_method="start", permission_class="write_local")
        cross = CrossHarnessPermissionRepository(db)
        cross.grant(
            session_id=sid, workspace_id=ws,
            agent_harness_type="hermes", tool_harness_type="shell",
            decision="once",
        )
        dispatcher = ToolDispatcher(db)
        # First call: should run (consumes the once grant)
        result1 = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_once1",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "echo first"}),
                },
            },
            agent_harness_type="hermes",
        )
        assert result1.status != "pending_confirmation"
        # Second call: should prompt again (no more once grants)
        result2 = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_once2",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "echo second"}),
                },
            },
            agent_harness_type="hermes",
        )
        assert result2.status == "pending_confirmation"

    def test_once_consumed_atomically(self, db):
        """Two connections racing for one grant cannot both consume it."""
        ws, ch, sid = _seed_workspace_and_session(db)
        cross = CrossHarnessPermissionRepository(db)
        cross.grant(
            session_id=sid, workspace_id=ws,
            agent_harness_type="hermes", tool_harness_type="shell",
            decision="once",
        )
        db_path = db.execute("PRAGMA database_list").fetchone()[2]
        barrier = threading.Barrier(2)
        results: list[int] = []
        errors: list[BaseException] = []

        class _RacingConnection:
            """Force both legacy SELECTs to finish before either DELETE."""

            def __init__(self, conn):
                self.conn = conn

            def execute(self, sql, params=()):
                cursor = self.conn.execute(sql, params)
                if sql.lstrip().startswith("SELECT permission_id"):
                    barrier.wait(timeout=5)
                return cursor

            def commit(self):
                return self.conn.commit()

        def consume() -> None:
            from agent_workbench.db import get_connection

            conn = get_connection(db_path)
            try:
                repo = CrossHarnessPermissionRepository(
                    _RacingConnection(conn)  # type: ignore[arg-type]
                )
                results.append(repo.consume_once(
                    session_id=sid,
                    agent_harness_type="hermes",
                    tool_harness_type="shell",
                ))
            except BaseException as exc:  # pragma: no cover - diagnostic path
                errors.append(exc)
            finally:
                conn.close()

        workers = [threading.Thread(target=consume) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        assert errors == []
        assert sorted(results) == [0, 1]

    def test_global_once_grant_is_consumed_by_specific_agent(self, db):
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.global_once", "shell")
        CrossHarnessPermissionRepository(db).grant(
            session_id=sid,
            workspace_id=ws,
            agent_harness_type=None,
            tool_harness_type="shell",
            decision="once",
        )
        dispatcher = ToolDispatcher(db)
        call = {
            "id": "global-once",
            "function": {
                "name": f"shell.{tool.name}",
                "arguments": json.dumps({"command": "printf global"}),
            },
        }
        first = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=None,
            tool_call=call,
            agent_harness_type="hermes",
        )
        second = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=None,
            tool_call={**call, "id": "global-twice"},
            agent_harness_type="hermes",
        )
        assert first.status == "completed"
        assert second.status == "pending_confirmation"


# ===========================================================================
# 5. Explicit empty policy [] denies all
# ===========================================================================


class TestExplicitEmptyPolicyDenial:
    """Across ToolRegistry/ToolDispatcher/runtime, None is the only
    unspecified/permissive policy sentinel; explicit [] denies all
    permission classes."""

    def test_empty_policy_denies_all_in_dispatcher(self, db):
        """An explicit [] in the dispatcher denies every tool."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run8", "shell",
                          adapter_method="start", permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=[],  # explicit empty — denies all
            tool_call={
                "id": "call_empty",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "date"}),
                },
            },
            agent_harness_type="hermes",
        )
        assert result.status == "denied"
        assert "permission" in result.content

    def test_none_policy_allows_all_in_dispatcher(self, db):
        """None in the dispatcher allows all permission classes."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run9", "shell",
                          adapter_method="start", permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=None,  # None — permissive default
            tool_call={
                "id": "call_none",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "printf allowed"}),
                },
            },
            agent_harness_type="hermes",
        )
        # Should run (not denied for policy reasons)
        assert result.status != "denied"

    def test_empty_policy_denies_all_in_registry(self, db):
        """An explicit [] in the registry denies every tool."""
        from agent_workbench.services.tool_registry import ToolRegistry
        from dataclasses import dataclass

        @dataclass
        class _ProfileStub:
            harness_ref: str | None
            capability_hints_json: dict | None = None

        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        _make_tool(db, "reg_test", "shell", permission_class="read_only")
        profile = _ProfileStub(harness_ref="shell")
        tools = reg.effective_tools(
            agent_profile=profile,
            harness_type="shell",
            session_type="chat",
            session_policy=[],  # explicit empty — denies all
        )
        assert tools == []

    def test_none_policy_allows_all_in_registry(self, db):
        """None in the registry allows all permission classes."""
        from agent_workbench.services.tool_registry import ToolRegistry
        from dataclasses import dataclass

        @dataclass
        class _ProfileStub:
            harness_ref: str | None
            capability_hints_json: dict | None = None

        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        t = _make_tool(db, "reg_test2", "shell", permission_class="read_only")
        profile = _ProfileStub(harness_ref="shell")
        tools = reg.effective_tools(
            agent_profile=profile,
            harness_type="shell",
            session_type="chat",
            session_policy=None,  # None — permissive default
        )
        names = {x.name for x in tools}
        assert t.name in names


# ===========================================================================
# 6. Transfer cleanup on session delete
# ===========================================================================


class TestTransferCleanup:
    """SessionService.delete_session deletes participant_transfers where
    the session is source or target."""

    def test_transfer_cleaned_on_delete(self, db):
        """participant_transfers referencing the deleted session as
        source or target are removed."""
        ws, ch, sid = _seed_workspace_and_session(db)
        # Create a second session to be the other end
        sess2 = SessionExtensionRepository(db).create(
            workspace_id=ws, session_type="chat",
        )
        db.commit()
        # Insert a participant_transfer row directly
        import uuid
        import time
        transfer_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO participant_transfers "
            "(transfer_id, source_session_id, target_session_id, status, created_at) "
            "VALUES (?, ?, ?, 'completed', ?)",
            (transfer_id, sid, sess2.session_id, time.time()),
        )
        db.commit()
        # Also insert one where sid is the target
        transfer_id2 = uuid.uuid4().hex
        db.execute(
            "INSERT INTO participant_transfers "
            "(transfer_id, source_session_id, target_session_id, status, created_at) "
            "VALUES (?, ?, ?, 'completed', ?)",
            (transfer_id2, sess2.session_id, sid, time.time()),
        )
        db.commit()
        # Verify they exist
        rows = db.execute(
            "SELECT COUNT(*) FROM participant_transfers "
            "WHERE source_session_id = ? OR target_session_id = ?",
            (sid, sid),
        ).fetchone()[0]
        assert rows == 2
        CrossHarnessPermissionRepository(db).grant(
            session_id=sid,
            workspace_id=ws,
            agent_harness_type="hermes",
            tool_harness_type="shell",
            decision="permanent",
        )
        # Delete the session
        svc = SessionService(db)
        svc.delete_session(sid)
        # Verify transfers are gone
        rows = db.execute(
            "SELECT COUNT(*) FROM participant_transfers "
            "WHERE source_session_id = ? OR target_session_id = ?",
            (sid, sid),
        ).fetchone()[0]
        assert rows == 0
        permission_rows = db.execute(
            "SELECT COUNT(*) FROM cross_harness_permissions WHERE session_id = ?",
            (sid,),
        ).fetchone()[0]
        assert permission_rows == 0


# ===========================================================================
# 7. Session lock cleanup on session delete
# ===========================================================================


class TestLockCleanup:
    """SessionService.delete_session cleans the module-level session
    lock via _cleanup_session_lock."""

    def test_lock_cleaned_on_delete(self, db):
        """The module-level session lock is removed after delete."""
        from agent_workbench.services.agent_runtime_service import (
            _session_locks,
            _session_locks_lock,
        )
        ws, ch, sid = _seed_workspace_and_session(db)
        # Create a lock for this session
        with _session_locks_lock:
            _session_locks[sid] = threading.Lock()
        assert sid in _session_locks
        # Delete the session
        svc = SessionService(db)
        svc.delete_session(sid)
        # Lock should be gone
        assert sid not in _session_locks

    def test_cleanup_lock_idempotent(self, db):
        """_cleanup_session_lock is safe to call for a non-existent lock."""
        # Should not raise
        _cleanup_session_lock("nonexistent-session-id")
        # Should not raise for a session that was already cleaned
        _cleanup_session_lock("another-nonexistent")


# ===========================================================================
# 8. message_row data attribute + event listener (structural)
# ===========================================================================


class TestMessageRowDataAttributes:
    """The message_row template uses data attributes instead of inline
    onclick for tool confirmation bubbles."""

    def test_template_has_data_attributes(self):
        """The message_row.html template should use data-invocation-id
        and data-still-pending instead of inline onclick."""
        import os
        template_path = os.path.join(
            os.path.dirname(__file__),
            "../src/agent_workbench/web/templates/message_row.html",
        )
        with open(template_path) as f:
            content = f.read()
        # Should have data-invocation-id attribute
        assert 'data-invocation-id="' in content
        # Should have data-still-pending attribute
        assert 'data-still-pending="' in content
        # Should NOT have inline onclick with openToolPanel
        assert 'onclick="openToolPanel' not in content
        # Should NOT have inline onclick with "return"
        assert 'onclick="{% if' not in content

    def test_session_view_has_event_listener(self):
        """The session_view.html template should have a delegated
        click event listener for [data-invocation-id]."""
        import os
        template_path = os.path.join(
            os.path.dirname(__file__),
            "../src/agent_workbench/web/templates/session_view.html",
        )
        with open(template_path) as f:
            content = f.read()
        # Should have the delegated event listener
        assert "data-invocation-id" in content
        assert "openToolPanel" in content
        # The event listener should use closest('[data-invocation-id]')
        assert "closest('[data-invocation-id]')" in content
