"""Tests for AgentProfileBindingRepository."""

import sqlite3

from agent_workbench.models.agent_profile import AgentProfileRepository
from agent_workbench.models.agent_profile_binding import (
    AgentProfileBinding,
    AgentProfileBindingRepository,
)


def _seed_profile(db: sqlite3.Connection) -> str:
    """Create a real agent profile so FK constraints pass."""
    repo = AgentProfileRepository(db)
    profile = repo.create(name="test-profile")
    return profile.agent_profile_id


def test_create_and_get_by_id(db: sqlite3.Connection):
    profile_id = _seed_profile(db)
    repo = AgentProfileBindingRepository(db)
    binding = repo.create(
        session_id="session-abc",
        agent_profile_id=profile_id,
    )
    assert binding.binding_id is not None
    assert binding.session_id == "session-abc"
    assert binding.agent_profile_id == profile_id
    assert binding.binding_version == "1"
    assert binding.created_from == "initial"

    fetched = repo.get_by_id(binding.binding_id)
    assert fetched is not None
    assert fetched.binding_id == binding.binding_id


def test_create_with_custom_values(db: sqlite3.Connection):
    profile_id = _seed_profile(db)
    repo = AgentProfileBindingRepository(db)
    binding = repo.create(
        session_id="s1",
        agent_profile_id=profile_id,
        binding_version="3",
        created_from="profile_change",
    )
    assert binding.binding_version == "3"
    assert binding.created_from == "profile_change"


def test_get_by_session(db: sqlite3.Connection):
    profile_id = _seed_profile(db)
    repo = AgentProfileBindingRepository(db)
    repo.create(session_id="s1", agent_profile_id=profile_id, created_from="initial")
    repo.create(session_id="s1", agent_profile_id=profile_id, created_from="profile_change")
    repo.create(session_id="s2", agent_profile_id=profile_id, created_from="initial")

    session_1 = repo.get_by_session("s1")
    assert len(session_1) == 2
    assert all(b.session_id == "s1" for b in session_1)

    session_2 = repo.get_by_session("s2")
    assert len(session_2) == 1

    assert repo.get_by_session("nonexistent") == []


def test_get_latest_for_session(db: sqlite3.Connection):
    profile_id = _seed_profile(db)
    repo = AgentProfileBindingRepository(db)
    repo.create(session_id="s1", agent_profile_id=profile_id, created_from="initial")
    import time as _time
    _time.sleep(0.01)
    latest = repo.create(
        session_id="s1",
        agent_profile_id=profile_id,
        created_from="profile_change",
    )

    result = repo.get_latest_for_session("s1")
    assert result is not None
    assert result.binding_id == latest.binding_id
    assert result.agent_profile_id == profile_id


def test_get_latest_for_session_empty(db: sqlite3.Connection):
    repo = AgentProfileBindingRepository(db)
    assert repo.get_latest_for_session("nonexistent") is None


def test_delete(db: sqlite3.Connection):
    profile_id = _seed_profile(db)
    repo = AgentProfileBindingRepository(db)
    binding = repo.create(session_id="s1", agent_profile_id=profile_id)
    assert repo.delete(binding.binding_id) is True
    assert repo.get_by_id(binding.binding_id) is None
    assert repo.delete(binding.binding_id) is False


def test_get_by_id_missing(db: sqlite3.Connection):
    repo = AgentProfileBindingRepository(db)
    assert repo.get_by_id("nonexistent") is None
