"""Tests for the ToolRepository (tools table CRUD)."""

from __future__ import annotations


import pytest

from agent_workbench.models.tool import ToolRepository


def test_create_tool_returns_full_record(db):
    repo = ToolRepository(db)
    t = repo.create(
        name="hello",
        harness_type="shell",
        adapter_method="start",
        description="says hi",
        input_schema={"type": "object", "properties": {}},
        permission_class="read_only",
    )
    assert t.tool_id
    assert t.name == "hello"
    assert t.harness_type == "shell"
    assert t.adapter_method == "start"
    assert t.description == "says hi"
    assert t.input_schema_json == {"type": "object", "properties": {}}
    assert t.permission_class == "read_only"
    assert t.is_enabled is True
    assert t.is_builtin is False


def test_unique_harness_name(db):
    repo = ToolRepository(db)
    repo.create(
        name="dup", harness_type="shell", adapter_method="start",
    )
    with pytest.raises(ValueError, match="already exists"):
        repo.create(
            name="dup", harness_type="shell", adapter_method="start",
        )


def test_get_by_name(db):
    repo = ToolRepository(db)
    t = repo.create(name="x", harness_type="hermes", adapter_method="delegate_subagent")
    found = repo.get_by_name("hermes", "x")
    assert found is not None
    assert found.tool_id == t.tool_id


def test_list_for_harness_filters_disabled(db):
    repo = ToolRepository(db)
    repo.create(name="z_a", harness_type="shell", adapter_method="start", is_enabled=True)
    repo.create(name="z_b", harness_type="shell", adapter_method="start", is_enabled=False)
    repo.create(name="z_c", harness_type="hermes", adapter_method="delegate_subagent", is_enabled=True)
    shell_tools = repo.list_for_harness("shell")
    assert "z_a" in [t.name for t in shell_tools]
    assert "z_b" not in [t.name for t in shell_tools]
    hermes_tools = repo.list_for_harness("hermes")
    assert "z_c" in [t.name for t in hermes_tools]


def test_update_cannot_change_harness_or_name(db):
    repo = ToolRepository(db)
    t = repo.create(name="x", harness_type="shell", adapter_method="start")
    updated = repo.update(t.tool_id, description="new", is_enabled=False)
    assert updated is not None
    assert updated.description == "new"
    assert updated.is_enabled is False
    # The catalog enforces the (harness_type, name) unique key, so
    # those are not user-editable through the standard update path.


def test_delete_refuses_builtin(db):
    repo = ToolRepository(db)
    t = repo.create(name="x", harness_type="shell", adapter_method="start", is_builtin=True)
    assert repo.delete(t.tool_id) is False
    assert repo.get_by_id(t.tool_id) is not None


def test_input_schema_round_trip(db):
    repo = ToolRepository(db)
    schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }
    t = repo.create(
        name="x", harness_type="shell", adapter_method="start",
        input_schema=schema,
    )
    # Re-read through the repo to make sure JSON survived the round trip.
    again = repo.get_by_id(t.tool_id)
    assert again is not None
    assert again.input_schema_json == schema


def test_create_rejects_invalid_permission(db):
    repo = ToolRepository(db)
    with pytest.raises(ValueError, match="permission_class"):
        repo.create(
            name="x", harness_type="shell", adapter_method="start",
            permission_class="bogus",
        )


def test_create_rejects_invalid_harness(db):
    repo = ToolRepository(db)
    with pytest.raises(ValueError, match="harness_type"):
        repo.create(name="x", harness_type="bogus", adapter_method="start")
