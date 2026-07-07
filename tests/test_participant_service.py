"""Tests for ParticipantService."""

from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.participant_service import ParticipantService
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.session_service import SessionService


def _seed_context(db):
    workspace_id = WorkspaceRepository(db).create(tenant_id="t1", name="WS").workspace_id
    session = SessionService(db).create_session(workspace_id=workspace_id, session_type="chat")
    profile = ProfileService(db).create_profile(
        name="chat-agent",
        provider="provider-mock-default",
        function="role-assistant",
        model="mock-model",
    )
    return workspace_id, session.session_id, profile.agent_profile_id


def test_add_participant_is_idempotent_by_agent_profile(db):
    _, session_id, agent_profile_id = _seed_context(db)
    svc = ParticipantService(db)
    first = svc.add_participant(session_id=session_id, agent_profile_id=agent_profile_id)
    second = svc.add_participant(session_id=session_id, agent_profile_id=agent_profile_id)
    assert first.participant_id == second.participant_id
    assert len(svc.list_active_participant_details(session_id)) == 1


def test_remove_participant_marks_removed(db):
    _, session_id, agent_profile_id = _seed_context(db)
    svc = ParticipantService(db)
    participant = svc.add_participant(session_id=session_id, agent_profile_id=agent_profile_id)
    svc.remove_participant(participant.participant_id)
    assert svc.list_active_participant_details(session_id) == []
