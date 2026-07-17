"""Tests for the cross-harness confirmation flow."""

from __future__ import annotations

import json


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
    extract_agent_harness_from_reason,
    reconstruct_tool_call,
)


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


class TestExtractAgentHarnessFromReason:
    def test_canonical_reason(self):
        reason = (
            "Tool 'shell.run_command' is outside the agent's configured "
            "harness 'hermes'; user must confirm."
        )
        assert extract_agent_harness_from_reason(reason) == "hermes"

    def test_no_marker(self):
        assert extract_agent_harness_from_reason("nope") is None

    def test_empty(self):
        assert extract_agent_harness_from_reason("") is None


class TestReconstructToolCall:
    def test_rebuilds_namespace(self, db):
        ws, sid = _seed_workspace_and_session(db)[:2]
        tool = _make_tool(db, "shell.run_command", "shell",
                          adapter_method="start", permission_class="write_local")
        inv = ToolInvocationRepository(db).create(
            session_id=sid,
            workspace_id=ws,
            tool_id=tool.tool_id,
            tool_name=tool.name,
            tool_harness_type="shell",
            arguments={"command": "date"},
        )
        call = reconstruct_tool_call(inv)
        assert call["function"]["name"] == f"shell.{tool.name}"
        args = json.loads(call["function"]["arguments"])
        assert args == {"command": "date"}


class TestCrossHarnessPermissionRepo:
    def test_grant_once(self, db):
        ws, ch, sid = _seed_workspace_and_session(db)
        repo = CrossHarnessPermissionRepository(db)
        p = repo.grant(
            session_id=sid,
            workspace_id=ws,
            agent_harness_type="hermes",
            tool_harness_type="shell",
            decision="once",
        )
        assert p.decision == "once"
        # Re-grant returns the existing row (idempotent).
        p2 = repo.grant(
            session_id=sid,
            workspace_id=ws,
            agent_harness_type="hermes",
            tool_harness_type="shell",
            decision="once",
        )
        assert p2.permission_id == p.permission_id

    def test_is_allowed_specific(self, db):
        ws, ch, sid = _seed_workspace_and_session(db)
        repo = CrossHarnessPermissionRepository(db)
        repo.grant(
            session_id=sid, workspace_id=ws,
            agent_harness_type="hermes", tool_harness_type="shell",
            decision="permanent",
        )
        assert repo.is_allowed(
            session_id=sid, agent_harness_type="hermes",
            tool_harness_type="shell",
        ) is True
        # Different agent harness — not allowed.
        assert repo.is_allowed(
            session_id=sid, agent_harness_type="opencode",
            tool_harness_type="shell",
        ) is False
        # Different tool harness — not allowed.
        assert repo.is_allowed(
            session_id=sid, agent_harness_type="hermes",
            tool_harness_type="ssh",
        ) is False

    def test_is_allowed_global(self, db):
        ws, ch, sid = _seed_workspace_and_session(db)
        repo = CrossHarnessPermissionRepository(db)
        repo.grant(
            session_id=sid, workspace_id=ws,
            agent_harness_type=None, tool_harness_type="shell",
            decision="permanent",
        )
        # Matches any agent harness because agent_harness_type is NULL.
        assert repo.is_allowed(
            session_id=sid, agent_harness_type="hermes",
            tool_harness_type="shell",
        ) is True
        assert repo.is_allowed(
            session_id=sid, agent_harness_type="opencode",
            tool_harness_type="shell",
        ) is True

    def test_consume_once_removes_once_rows(self, db):
        ws, ch, sid = _seed_workspace_and_session(db)
        repo = CrossHarnessPermissionRepository(db)
        repo.grant(
            session_id=sid, workspace_id=ws,
            agent_harness_type="hermes", tool_harness_type="shell",
            decision="once",
        )
        # Permanent row is preserved.
        repo.grant(
            session_id=sid, workspace_id=ws,
            agent_harness_type="hermes", tool_harness_type="shell",
            decision="permanent",
        )
        removed = repo.consume_once(
            session_id=sid, agent_harness_type="hermes",
            tool_harness_type="shell",
        )
        assert removed == 1
        # 'once' is gone, 'permanent' is still there.
        assert repo.is_allowed(
            session_id=sid, agent_harness_type="hermes",
            tool_harness_type="shell",
        ) is True
        # But if we delete the permanent one, nothing is allowed.
        perms = repo.list_for_session(sid)
        for p in perms:
            repo.delete(p.permission_id)
        assert repo.is_allowed(
            session_id=sid, agent_harness_type="hermes",
            tool_harness_type="shell",
        ) is False


class TestDispatcherCrossHarnessCheck:
    def test_cross_harness_call_is_pending(self, db):
        """A hermes agent calling a shell tool is pending_confirmation."""
        ws, ch, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run_command", "shell",
                          adapter_method="start", permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_xh",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "date"}),
                },
            },
            agent_harness_type="hermes",
        )
        assert result.status == "pending_confirmation"
        assert result.invocation_id
        inv = ToolInvocationRepository(db).get_by_id(result.invocation_id)
        assert inv is not None
        assert inv.status == "pending_confirmation"
        assert inv.requires_confirmation is True
        assert "hermes" in (inv.confirmation_reason or "")
        assert "shell" in (inv.confirmation_reason or "")

    def test_same_harness_call_runs_immediately(self, db):
        """A hermes agent calling a hermes tool runs without prompting."""
        ws, ch, sid = _seed_workspace_and_session(db)
        # Use the seeded hermes.run_command builtin.
        dispatcher = ToolDispatcher(db)
        hermes_tool = next(
            t for t in ToolRepository(db).list_for_harness("hermes")
            if t.name == "run_command"
        )
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_same",
                "function": {
                    "name": f"hermes.{hermes_tool.name}",
                    "arguments": json.dumps({
                        "command": "date", "harness_run_id": "any",
                    }),
                },
            },
            agent_harness_type="hermes",
        )
        # The hermes.run_command tool requires harness_run_id, so it
        # auto-spawns.  Status should be 'completed' (or at least not
        # 'pending_confirmation').
        assert result.status != "pending_confirmation"

    def test_no_agent_harness_skips_check(self, db):
        """When agent_harness_type is None, no cross-harness check."""
        ws, ch, sid = _seed_workspace_and_session(db)
        # A shell tool is called but the dispatcher doesn't know the
        # agent's harness.
        tool = _make_tool(db, "shell.run_command", "shell",
                          adapter_method="start", permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_no",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "date"}),
                },
            },
            agent_harness_type=None,
        )
        # No cross-harness check; the call either runs or is denied
        # for permission/policy reasons.
        assert result.status != "pending_confirmation"

    def test_allowed_perm_skips_prompt(self, db):
        """If a 'permanent' row exists, the call runs without prompt."""
        ws, ch, sid = _seed_workspace_and_session(db)
        # Pre-grant the permission.
        CrossHarnessPermissionRepository(db).grant(
            session_id=sid, workspace_id=ws,
            agent_harness_type="hermes", tool_harness_type="shell",
            decision="permanent",
        )
        tool = _make_tool(db, "shell.run_command", "shell",
                          adapter_method="start", permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_perm",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "echo allowed"}),
                },
            },
            agent_harness_type="hermes",
        )
        # The cross-harness check passes because of the perm row.
        assert result.status != "pending_confirmation"
