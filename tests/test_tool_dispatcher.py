"""Tests for ToolDispatcher — the bridge between provider tool_calls
and adapter methods."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.tool import ToolRepository
from agent_workbench.models.tool_invocation import ToolInvocationRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.tool_dispatcher import ToolDispatcher


def _seed_workspace_and_session(db):
    ws = WorkspaceRepository(db).create(tenant_id="t1", name="t")
    db.commit()
    ch = ChannelRepository(db).create(
        workspace_id=ws.workspace_id,
        channel_kind="chat",
        title="t",
    )
    db.commit()
    sess = SessionExtensionRepository(db).create(
        workspace_id=ws.workspace_id,
        session_type="chat",
    )
    db.commit()
    return ws.workspace_id, sess.session_id


def _make_tool(db, name, harness_type, adapter_method="start",
               permission_class="write_local", is_enabled=True):
    """Create a tool with a UUID-suffixed name so it doesn't collide
    with the conftest's builtin seed rows (shell.run_command, etc)."""
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


class TestDispatchShellRunCommand:
    def test_dispatch_runs_shell_and_persists_invocation(self, db):
        ws, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run_command", "shell", adapter_method="start",
                           permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_1",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "printf from-dispatch"}),
                },
            },
        )
        assert result.status == "completed"
        assert result.harness_type == "shell"
        assert result.harness_run_id
        run = HarnessRunRepository(db).get_by_id(result.harness_run_id)
        assert run is not None
        assert run.session_id == sid
        assert result.invocation_id
        inv = ToolInvocationRepository(db).get_by_id(result.invocation_id)
        assert inv is not None
        assert inv.status == "completed"
        assert "from-dispatch" in (inv.result_text or "")

    def test_dispatch_with_unknown_tool_returns_denied(self, db):
        ws, sid = _seed_workspace_and_session(db)
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["write_local"],
            tool_call={
                "id": "call_x",
                "function": {
                    "name": "shell.does_not_exist",
                    "arguments": "{}",
                },
            },
        )
        assert result.status == "denied"
        assert "not registered" in result.content


class TestDispatchPermissions:
    def test_write_tool_denied_in_chat_session(self, db):
        ws, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run_command", "shell", adapter_method="start",
                           permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only"],  # chat session
            tool_call={
                "id": "call_p",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "echo denied"}),
                },
            },
        )
        assert result.status == "denied"
        assert "permission" in result.content

    def test_disabled_tool_denied(self, db):
        ws, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run_command", "shell", adapter_method="start",
                           permission_class="write_local", is_enabled=False)
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["write_local"],
            tool_call={
                "id": "call_d",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({"command": "echo x"}),
                },
            },
        )
        assert result.status == "denied"
        assert "disabled" in result.content


class TestDispatchHermesDelegateStub:
    def test_hermes_delegate_returns_not_implemented(self, db):
        ws, sid = _seed_workspace_and_session(db)
        # Builtin catalog has hermes.delegate_subagent as read_only.
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["read_only", "write_local"],
            tool_call={
                "id": "call_h",
                "function": {
                    "name": "hermes.delegate_subagent",
                    "arguments": json.dumps({"task": "do a thing"}),
                },
            },
        )
        # The stub is honest: it returns "failed" with a precise error.
        assert result.status == "failed"
        assert "not yet implemented" in result.content


class TestDispatchMalformedArguments:
    def test_invalid_json_arguments_is_failed(self, db):
        ws, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run_command", "shell", adapter_method="start",
                           permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["write_local"],
            tool_call={
                "id": "call_j",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": "{ not json",
                },
            },
        )
        assert result.status == "failed"
        assert "JSON" in result.content

    def test_missing_command_argument(self, db):
        ws, sid = _seed_workspace_and_session(db)
        tool = _make_tool(db, "shell.run_command", "shell", adapter_method="start",
                           permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["write_local"],
            tool_call={
                "id": "call_m",
                "function": {
                    "name": f"shell.{tool.name}",
                    "arguments": json.dumps({}),  # no command
                },
            },
        )
        assert result.status == "failed"
        assert "command" in result.content


class TestDispatchMalformedName:
    def test_no_namespace_is_denied(self, db):
        ws, sid = _seed_workspace_and_session(db)
        _make_tool(db, "x", "shell", adapter_method="start",
                   permission_class="write_local")
        dispatcher = ToolDispatcher(db)
        result = dispatcher.dispatch(
            session_id=sid,
            workspace_id=ws,
            session_policy=["write_local"],
            tool_call={
                "id": "call_n",
                "function": {
                    "name": "no_namespace_here",
                    "arguments": "{}",
                },
            },
        )
        assert result.status == "denied"
        assert "Malformed" in result.content


class TestDispatchHermesRunRecovery:
    def _fake_start(self, adapter, *, workspace_id, session_id, command):
        hr = adapter._repo.create(
            workspace_id=workspace_id,
            session_id=session_id,
            harness_type=adapter.adapter_type,
            status="running",
            control_capabilities=adapter.capabilities_dict(),
        )
        adapter._sessions[hr.harness_run_id] = {
            "process": object(),
            "session_id": session_id,
            "process_id": "1234",
            "stdout": "",
            "stderr": "",
            "command": command,
            "pgid": None,
        }
        return hr.harness_run_id

    def test_invalid_hermes_run_id_retries_with_auto_spawn(self, db):
        ws, sid = _seed_workspace_and_session(db)
        dispatcher = ToolDispatcher(db)
        seen_run_ids = []

        def fake_start(adapter, *, workspace_id, session_id, command, **kwargs):
            return self._fake_start(
                adapter,
                workspace_id=workspace_id,
                session_id=session_id,
                command=command,
            )

        def fake_execute_shell(adapter, harness_run_id, command, **kwargs):
            seen_run_ids.append(harness_run_id)
            if harness_run_id == "default":
                raise RuntimeError(f"No Hermes session for {harness_run_id}")
            return SimpleNamespace(stdout=f"ran:{command}", stderr="")

        with patch(
            "agent_workbench.adapters.hermes_adapter.HermesAdapter.start",
            new=fake_start,
        ), patch(
            "agent_workbench.adapters.hermes_adapter.HermesAdapter.execute_shell",
            new=fake_execute_shell,
        ):
            result = dispatcher.dispatch(
                session_id=sid,
                workspace_id=ws,
                session_policy=["read_only", "write_local"],
                tool_call={
                    "id": "call_recover",
                    "function": {
                        "name": "hermes.run_command",
                        "arguments": json.dumps({
                            "harness_run_id": "default",
                            "command": "date",
                        }),
                    },
                },
                agent_harness_type="hermes",
            )

        assert result.status == "completed"
        assert result.harness_run_id
        assert seen_run_ids[0] == "default"
        assert seen_run_ids[1] == result.harness_run_id
        inv = ToolInvocationRepository(db).get_by_id(result.invocation_id)
        assert inv is not None
        assert inv.status == "completed"
        assert inv.harness_run_id == result.harness_run_id
        assert "ran:date" in result.content

    def test_invalid_hermes_write_file_run_id_retries_with_auto_spawn(self, db):
        ws, sid = _seed_workspace_and_session(db)
        dispatcher = ToolDispatcher(db)
        seen_run_ids = []

        def fake_start(adapter, *, workspace_id, session_id, command, **kwargs):
            return self._fake_start(
                adapter,
                workspace_id=workspace_id,
                session_id=session_id,
                command=command,
            )

        def fake_write_file(adapter, harness_run_id, path, data, **kwargs):
            seen_run_ids.append(harness_run_id)
            if harness_run_id == "stale-run":
                raise RuntimeError(f"No Hermes session for {harness_run_id}")
            return path

        with patch(
            "agent_workbench.adapters.hermes_adapter.HermesAdapter.start",
            new=fake_start,
        ), patch(
            "agent_workbench.adapters.hermes_adapter.HermesAdapter.write_file",
            new=fake_write_file,
        ):
            result = dispatcher.dispatch(
                session_id=sid,
                workspace_id=ws,
                session_policy=["read_only", "write_local"],
                tool_call={
                    "id": "call_write_recover",
                    "function": {
                        "name": "hermes.write_file",
                        "arguments": json.dumps({
                            "harness_run_id": "stale-run",
                            "path": "/tmp/example.txt",
                            "data": "hello",
                        }),
                    },
                },
                agent_harness_type="hermes",
            )

        assert result.status == "completed"
        assert result.harness_run_id
        assert seen_run_ids[0] == "stale-run"
        assert seen_run_ids[1] == result.harness_run_id
        inv = ToolInvocationRepository(db).get_by_id(result.invocation_id)
        assert inv is not None
        assert inv.status == "completed"
        assert inv.harness_run_id == result.harness_run_id
        assert "/tmp/example.txt" in result.content
