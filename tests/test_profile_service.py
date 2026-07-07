"""Tests for ProfileService."""

import pytest

from agent_workbench.models.agent_profile import AgentProfile
from agent_workbench.models.agent_profile_binding import AgentProfileBinding
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.profile_service import (
    ProfileNotFoundError,
    ProfileService,
)
from agent_workbench.services.session_service import SessionService


@pytest.fixture
def workspace_id(db):
    ws = WorkspaceRepository(db).create(tenant_id="t1", name="WS")
    return ws.workspace_id


@pytest.fixture
def session_id(workspace_id, db):
    s = SessionService(db).create_session(
        workspace_id=workspace_id, session_type="chat"
    )
    return s.session_id


@pytest.fixture
def svc(db):
    return ProfileService(db)


def _make_profile(svc, **overrides):
    defaults = dict(
        name="researcher",
        provider="ollama",
        model="llama3",
        perspective="balanced",
        function="researcher",
        harness="hermes",
    )
    defaults.update(overrides)
    return svc.create_profile(**defaults)


class TestCreateProfile:
    def test_create_minimal(self, svc):
        p = svc.create_profile(name="minimal")
        assert isinstance(p, AgentProfile)
        assert p.name == "minimal"
        assert p.version == "1"
        assert p.provider_ref is None

    def test_create_with_friendly_fields(self, svc):
        p = _make_profile(svc)
        assert p.name == "researcher"
        assert p.provider_ref == "ollama"
        assert p.model_ref == "llama3"
        assert p.perspective_ref == "balanced"
        assert p.function_ref == "researcher"
        assert p.harness_ref == "hermes"

    def test_create_with_capability_hints(self, svc):
        p = svc.create_profile(
            name="x",
            capability_hints={"tools": ["search"], "max_tokens": 4096},
        )
        assert p.capability_hints_json == {
            "tools": ["search"],
            "max_tokens": 4096,
        }

    def test_create_with_kwarg_friendly_name(self, svc):
        p = svc.create_profile(
            name="x",
            provider="anthropic",
            model="claude",
            perspective="formal",
            function="critic",
        )
        assert p.function_ref == "critic"
        assert p.perspective_ref == "formal"
        assert p.model_ref == "claude"


class TestGetProfile:
    def test_get_existing(self, svc):
        created = _make_profile(svc)
        fetched = svc.get_profile(created.agent_profile_id)
        assert fetched.agent_profile_id == created.agent_profile_id

    def test_get_missing_raises(self, svc):
        with pytest.raises(ProfileNotFoundError):
            svc.get_profile("nope")


class TestListProfiles:
    def test_list_empty(self, svc):
        assert svc.list_profiles() == []

    def test_list_returns_all(self, svc):
        _make_profile(svc, name="a")
        _make_profile(svc, name="b")
        _make_profile(svc, name="c")
        names = sorted(p.name for p in svc.list_profiles())
        assert names == ["a", "b", "c"]


class TestBindProfile:
    def test_bind_initial(self, svc, session_id):
        p = _make_profile(svc)
        b = svc.bind_profile(session_id, p.agent_profile_id)
        assert isinstance(b, AgentProfileBinding)
        assert b.session_id == session_id
        assert b.agent_profile_id == p.agent_profile_id
        assert b.created_from == "initial"
        assert b.binding_version == "1"

    def test_bind_missing_session_raises(self, svc):
        p = _make_profile(svc)
        with pytest.raises(ProfileNotFoundError):
            svc.bind_profile("nope", p.agent_profile_id)

    def test_bind_missing_profile_raises(self, svc, session_id):
        with pytest.raises(ProfileNotFoundError):
            svc.bind_profile(session_id, "nope")

    def test_bind_invalid_created_from_raises(self, svc, session_id):
        p = _make_profile(svc)
        with pytest.raises(ValueError, match="Invalid created_from"):
            svc.bind_profile(
                session_id, p.agent_profile_id, created_from="bogus"
            )

    def test_bind_creates_new_row_each_time(self, svc, session_id):
        p1 = _make_profile(svc, name="a")
        p2 = _make_profile(svc, name="b")
        b1 = svc.bind_profile(session_id, p1.agent_profile_id)
        b2 = svc.bind_profile(
            session_id, p2.agent_profile_id, created_from="profile_change"
        )
        assert b1.binding_id != b2.binding_id
        all_b = svc.list_bindings(session_id)
        assert len(all_b) == 2


class TestChangeProfile:
    def test_change_creates_new_binding(self, svc, session_id):
        p1 = _make_profile(svc, name="p1")
        p2 = _make_profile(svc, name="p2")
        b1 = svc.bind_profile(session_id, p1.agent_profile_id)
        b2 = svc.change_profile(session_id, p2.agent_profile_id)
        # New binding, profile_change, does not modify old binding
        assert b2.binding_id != b1.binding_id
        assert b2.created_from == "profile_change"
        assert b2.agent_profile_id == p2.agent_profile_id
        # Old binding is intact.
        old = svc.bindings.get_by_id(b1.binding_id)
        assert old is not None
        assert old.agent_profile_id == p1.agent_profile_id
        assert old.created_from == "initial"

    def test_get_current_binding_returns_latest(self, svc, session_id):
        p1 = _make_profile(svc, name="p1")
        p2 = _make_profile(svc, name="p2")
        svc.bind_profile(session_id, p1.agent_profile_id)
        b2 = svc.change_profile(session_id, p2.agent_profile_id)
        current = svc.get_current_binding(session_id)
        assert current is not None
        assert current.binding_id == b2.binding_id

    def test_get_current_binding_no_bindings(self, svc, session_id):
        assert svc.get_current_binding(session_id) is None
