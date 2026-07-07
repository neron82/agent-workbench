"""Tests for ToolRegistry negotiation + OpenAI schema builder."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_workbench.models.tool import Tool, ToolRepository
from agent_workbench.services.tool_registry import ToolRegistry


@dataclass
class _ProfileStub:
    """Minimum needed by ToolRegistry — just the fields it actually reads."""
    harness_ref: str | None
    capability_hints_json: dict | None = None


def _make_tool(repo, name, harness_type, permission_class, is_enabled=True):
    """Create a tool with a unique name so it doesn't collide with
    builtin seed rows.  We append a UUID-suffix to keep test isolation
    even when the conftest has already seeded shell/hermes builtins."""
    import uuid
    unique = f"{name}_{uuid.uuid4().hex[:8]}"
    return repo.create(
        name=unique,
        harness_type=harness_type,
        adapter_method="start",
        description=f"tool {name}",
        input_schema={"type": "object", "properties": {}},
        permission_class=permission_class,
        is_enabled=is_enabled,
    )


class TestEffectiveTools:
    def test_no_harness_returns_empty(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        profile = _ProfileStub(harness_ref=None)
        assert reg.effective_tools(
            agent_profile=profile, harness_type=None, session_type="chat",
        ) == []

    def test_unknown_harness_returns_empty(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        profile = _ProfileStub(harness_ref="bogus")
        assert reg.effective_tools(
            agent_profile=profile, harness_type="bogus", session_type="chat",
        ) == []

    def test_chat_session_blocks_write_tools(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        ro = _make_tool(repo, "ro", "shell", "read_only")
        wl = _make_tool(repo, "wl", "shell", "write_local")
        de = _make_tool(repo, "de", "shell", "destructive")
        profile = _ProfileStub(harness_ref="shell")
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="chat",
        )
        names = {t.name for t in tools}
        # The freshly-created tools must be filtered to the read-only one,
        # but we also expect the builtin shell.run_command to be filtered
        # out by the chat policy.
        assert ro.name in names
        assert wl.name not in names
        assert de.name not in names
        assert "run_command" not in names

    def test_work_session_allows_everything(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        ro = _make_tool(repo, "ro", "shell", "read_only")
        wl = _make_tool(repo, "wl", "shell", "write_local")
        de = _make_tool(repo, "de", "shell", "destructive")
        profile = _ProfileStub(harness_ref="shell")
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="work",
        )
        names = {t.name for t in tools}
        assert ro.name in names
        assert wl.name in names
        assert de.name in names
        # The builtin shell.run_command (write_local) is also exposed.
        assert "run_command" in names

    def test_research_session_allows_local_writes(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        ro = _make_tool(repo, "ro", "shell", "read_only")
        wl = _make_tool(repo, "wl", "shell", "write_local")
        wr = _make_tool(repo, "wr", "shell", "write_remote")
        de = _make_tool(repo, "de", "shell", "destructive")
        profile = _ProfileStub(harness_ref="shell")
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="research",
        )
        names = {t.name for t in tools}
        assert ro.name in names
        assert wl.name in names
        assert wr.name not in names
        assert de.name not in names

    def test_explicit_policy_overrides_session_type(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        ro = _make_tool(repo, "ro", "shell", "read_only")
        wl = _make_tool(repo, "wl", "shell", "write_local")
        profile = _ProfileStub(harness_ref="shell")
        tools = reg.effective_tools(
            agent_profile=profile,
            harness_type="shell",
            session_type="chat",  # would normally block wl
            session_policy=["read_only", "write_local"],
        )
        names = {t.name for t in tools}
        assert ro.name in names
        assert wl.name in names

    def test_allowed_tools_whitelist(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        a = _make_tool(repo, "a", "shell", "read_only")
        b = _make_tool(repo, "b", "shell", "read_only")
        c = _make_tool(repo, "c", "shell", "read_only")
        profile = _ProfileStub(
            harness_ref="shell",
            capability_hints_json={"allowed_tools": [a.name, b.name]},
        )
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="work",
        )
        names = {t.name for t in tools}
        assert a.name in names
        assert b.name in names
        assert c.name not in names
        # The builtin run_command is not in the allowed list either.
        assert "run_command" not in names

    def test_denied_tools_blacklist(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        a = _make_tool(repo, "a", "shell", "read_only")
        b = _make_tool(repo, "b", "shell", "read_only")
        profile = _ProfileStub(
            harness_ref="shell",
            capability_hints_json={"denied_tools": [a.name]},
        )
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="work",
        )
        names = {t.name for t in tools}
        assert a.name not in names
        assert b.name in names

    def test_other_harness_not_leaked(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        shell_one = _make_tool(repo, "shell_one", "shell", "read_only")
        hermes_one = _make_tool(repo, "hermes_one", "hermes", "read_only")
        profile = _ProfileStub(harness_ref="shell")
        tools = reg.effective_tools(
            agent_profile=profile, harness_type="shell", session_type="work",
        )
        names = {t.name for t in tools}
        assert shell_one.name in names
        assert hermes_one.name not in names


class TestOpenAISchema:
    def test_to_openai_tools_namespaces(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        a = _make_tool(repo, "run", "shell", "read_only")
        b = _make_tool(repo, "run", "hermes", "read_only")
        schema = reg.to_openai_tools([a, b])
        names = {s["function"]["name"] for s in schema}
        # Both have a short name of "run" but the registry namespaces them
        # by harness_type so the provider can disambiguate.
        assert f"shell.{a.name}" in names
        assert f"hermes.{b.name}" in names

    def test_to_openai_tools_includes_description_and_schema(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        t = repo.create(
            name="custom_run",
            harness_type="shell",
            adapter_method="start",
            description="runs a command",
            input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
            permission_class="read_only",
        )
        schema = reg.to_openai_tools([t])
        assert schema[0]["type"] == "function"
        assert schema[0]["function"]["name"] == f"shell.{t.name}"
        assert schema[0]["function"]["description"] == "runs a command"
        assert schema[0]["function"]["parameters"]["properties"]["command"]["type"] == "string"

    def test_to_openai_tools_empty_list(self, db):
        repo = ToolRepository(db)
        reg = ToolRegistry(repo)
        assert reg.to_openai_tools([]) == []
