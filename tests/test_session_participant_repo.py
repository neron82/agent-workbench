"""Tests for SessionParticipantRepository."""

from agent_workbench.models.agent_profile import AgentProfileRepository
from agent_workbench.models.session_participant import SessionParticipantRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.session_service import SessionService


def _seed_session_and_binding(db):
    workspace_id = WorkspaceRepository(db).create(tenant_id="t1", name="WS").workspace_id
    session = SessionService(db).create_session(workspace_id=workspace_id, session_type="chat")
    profile = AgentProfileRepository(db).create(name="repo-agent")
    binding = ProfileService(db).bind_profile(session.session_id, profile.agent_profile_id)
    return workspace_id, session.session_id, binding.binding_id


def test_create_and_list_active(db):
    workspace_id, session_id, binding_id = _seed_session_and_binding(db)
    repo = SessionParticipantRepository(db)
    participant = repo.create(
        workspace_id=workspace_id,
        session_id=session_id,
        binding_id=binding_id,
    )
    active = repo.list_active(session_id)
    assert len(active) == 1
    assert active[0].participant_id == participant.participant_id


def test_remove_hides_from_active_list(db):
    workspace_id, session_id, binding_id = _seed_session_and_binding(db)
    repo = SessionParticipantRepository(db)
    participant = repo.create(
        workspace_id=workspace_id,
        session_id=session_id,
        binding_id=binding_id,
    )
    removed = repo.set_removed(participant.participant_id)
    assert removed is not None
    assert repo.list_active(session_id) == []
    assert len(repo.list_for_session(session_id)) == 1
