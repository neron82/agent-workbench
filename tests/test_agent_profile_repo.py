"""Tests for AgentProfileRepository."""

import sqlite3

from agent_workbench.models.agent_profile import AgentProfile, AgentProfileRepository


def test_create_and_get_by_id(db: sqlite3.Connection):
    repo = AgentProfileRepository(db)
    profile = repo.create(name="coder", provider_ref="openai", model_ref="gpt-4")
    assert profile.agent_profile_id is not None
    assert profile.name == "coder"
    assert profile.version == "1"
    assert profile.provider_ref == "openai"

    fetched = repo.get_by_id(profile.agent_profile_id)
    assert fetched is not None
    assert fetched.name == "coder"
    assert fetched.model_ref == "gpt-4"


def test_create_with_capability_hints(db: sqlite3.Connection):
    repo = AgentProfileRepository(db)
    profile = repo.create(
        name="researcher",
        capability_hints_json={"tools": ["web_search", "arxiv"]},
    )
    assert profile.capability_hints_json == {"tools": ["web_search", "arxiv"]}

    fetched = repo.get_by_id(profile.agent_profile_id)
    assert fetched is not None
    assert fetched.capability_hints_json == {"tools": ["web_search", "arxiv"]}


def test_list_all(db: sqlite3.Connection):
    repo = AgentProfileRepository(db)
    repo.create(name="coder", provider_ref="openai")
    repo.create(name="reviewer", provider_ref="anthropic")

    all_profiles = repo.list_all()
    assert len(all_profiles) == 2
    names = {p.name for p in all_profiles}
    assert "coder" in names
    assert "reviewer" in names


def test_update_creates_new_version(db: sqlite3.Connection):
    repo = AgentProfileRepository(db)
    original = repo.create(name="coder", model_ref="gpt-4")
    old_id = original.agent_profile_id

    updated = repo.update(
        old_id,
        model_ref="gpt-4o",
        version=None,  # auto-increment
    )
    assert updated is not None
    assert updated.agent_profile_id != old_id
    assert updated.version == "2"
    assert updated.model_ref == "gpt-4o"
    assert updated.name == "coder"  # preserved from original


def test_update_preserves_unchanged_fields(db: sqlite3.Connection):
    repo = AgentProfileRepository(db)
    original = repo.create(
        name="coder",
        provider_ref="openai",
        model_ref="gpt-4",
        perspective_ref="technical",
    )
    updated = repo.update(original.agent_profile_id, model_ref="gpt-4o")
    assert updated is not None
    assert updated.provider_ref == "openai"
    assert updated.perspective_ref == "technical"


def test_update_multiple_times(db: sqlite3.Connection):
    repo = AgentProfileRepository(db)
    original = repo.create(name="coder", model_ref="gpt-4")
    v2 = repo.update(original.agent_profile_id, model_ref="gpt-4o")
    v3 = repo.update(v2.agent_profile_id, model_ref="gpt-5")
    assert v3 is not None
    assert v3.version == "3"
    assert v3.model_ref == "gpt-5"


def test_update_nonexistent(db: sqlite3.Connection):
    repo = AgentProfileRepository(db)
    result = repo.update("nonexistent", model_ref="gpt-4o")
    assert result is None


def test_delete(db: sqlite3.Connection):
    repo = AgentProfileRepository(db)
    profile = repo.create(name="coder")
    assert repo.delete(profile.agent_profile_id) is True
    assert repo.get_by_id(profile.agent_profile_id) is None
    assert repo.delete(profile.agent_profile_id) is False


def test_get_by_name(db: sqlite3.Connection):
    repo = AgentProfileRepository(db)
    repo.create(name="coder", model_ref="gpt-4")
    repo.create(name="reviewer", model_ref="claude")
    # Create a v2 of "coder"
    v1 = repo.create(name="coder", model_ref="gpt-4o")

    results = repo.get_by_name("coder")
    assert len(results) == 2
    assert all(p.name == "coder" for p in results)

    assert len(repo.get_by_name("nonexistent")) == 0


def test_get_by_id_missing(db: sqlite3.Connection):
    repo = AgentProfileRepository(db)
    assert repo.get_by_id("nonexistent") is None
