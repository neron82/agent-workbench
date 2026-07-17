"""Builtin tool catalog.

These rows are seeded on every app boot.  Users can disable individual
tools via the settings UI but cannot delete builtin rows (the
``ToolRepository.delete`` method refuses to delete ``is_builtin=1``).

Why seeded this way instead of as a static Python list?
- The catalog is a first-class product entity, queryable like any other
- The settings UI can show the same rows the runtime sees
- A user who wants to add a new shell command as a tool can do it via
  the same UI (not yet wired, but the schema supports it)
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_workbench.models.tool import ToolRepository


BUILTIN_TOOLS: tuple[dict[str, Any], ...] = (
    # shell harness
    {
        "name": "run_command",
        "harness_type": "shell",
        "adapter_method": "start",
        "description": (
            "Run a shell command on the local workbench host and "
            "return its stdout."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
            },
            "required": ["command"],
        },
        "permission_class": "write_local",
    },
    # hermes harness — these are first-class stubs that return a
    # precise "not yet implemented" error.  When the product layer
    # implements them, only the ``adapter_method`` body needs to
    # change; the catalog entry stays.
    {
        "name": "delegate_subagent",
        "harness_type": "hermes",
        "adapter_method": "delegate_subagent",
        "description": (
            "Delegate a sub-task to a hermes subagent.  Currently a "
            "read-only stub: the product layer doesn't implement "
            "subagent delegation yet, so this tool returns a clear "
            "'not implemented' error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The sub-task description.",
                },
            },
            "required": ["task"],
        },
        "permission_class": "read_only",
        "is_enabled": False,  # disabled by default — not yet implemented
    },
    {
        "name": "run_command",
        "harness_type": "hermes",
        "adapter_method": "execute_shell",
        "description": (
            "Run a shell command via a Hermes harness session. If "
            "``harness_run_id`` is omitted, Agent Workbench auto-spawns "
            "a Hermes session first and returns its ``harness_run_id``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "harness_run_id": {
                    "type": "string",
                    "description": (
                        "Optional ID of an existing Hermes HarnessRun. "
                        "If omitted, Agent Workbench auto-spawns one."
                    ),
                },
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
            },
            "required": ["command"],
        },
        "permission_class": "write_local",
    },
    {
        "name": "write_file",
        "harness_type": "hermes",
        "adapter_method": "write_file",
        "description": (
            "Write a file via a Hermes harness session. If "
            "``harness_run_id`` is omitted, Agent Workbench auto-spawns "
            "a Hermes session first and returns its ``harness_run_id``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "harness_run_id": {
                    "type": "string",
                    "description": (
                        "Optional ID of an existing Hermes HarnessRun. "
                        "If omitted, Agent Workbench auto-spawns one."
                    ),
                },
                "path": {"type": "string"},
                "data": {"type": "string"},
            },
            "required": ["path", "data"],
        },
        "permission_class": "write_local",
    },
)


def seed_builtin_tools(conn: sqlite3.Connection) -> int:
    """Idempotently insert builtin tools.  Returns the number of new rows."""
    repo = ToolRepository(conn)
    inserted = 0
    for spec in BUILTIN_TOOLS:
        existing = repo.get_by_name(spec["harness_type"], spec["name"])
        if existing is not None:
            repo.update(
                existing.tool_id,
                description=spec["description"],
                input_schema=spec["input_schema"],
                permission_class=spec["permission_class"],
            )
            continue
        repo.create(
            name=spec["name"],
            harness_type=spec["harness_type"],
            adapter_method=spec["adapter_method"],
            description=spec["description"],
            input_schema=spec["input_schema"],
            permission_class=spec["permission_class"],
            is_enabled=spec.get("is_enabled", True),
            is_builtin=True,
        )
        inserted += 1
    return inserted
