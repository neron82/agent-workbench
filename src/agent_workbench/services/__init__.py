"""Service layer for Agent Workbench.

Services compose one or more repositories from
:mod:`agent_workbench.models` to express product-layer use cases
(forking a session, orchestrating a run, routing a message, …).
They own the transaction boundary and any cross-entity invariants
that no single repository can enforce on its own.
"""

from agent_workbench.services.agent_runtime_service import AgentRuntimeService
from agent_workbench.services.artifact_verifier import ArtifactVerifier
from agent_workbench.services.fork_service import ForkService
from agent_workbench.services.participant_service import ParticipantService
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.provider_service import ProviderService
from agent_workbench.services.replay_service import ReplayService
from agent_workbench.services.review_service import (
    ReviewService,
    ReviewServiceError,
)
from agent_workbench.services.role_service import RoleService
from agent_workbench.services.tool_dispatcher import (
    ALLOWED_ADAPTER_METHODS,
    DispatchResult,
    ToolDeniedError,
    ToolDispatcher,
    ToolDispatchError,
)
from agent_workbench.services.tool_registry import (
    DEFAULT_SESSION_POLICIES,
    ToolRegistry,
)
from agent_workbench.services.transcript_service import (
    TranscriptService,
    pgid_of,
)
from agent_workbench.services.verification_service import (
    REPLAY_EQUIVALENCE_NOTE,
    VERIFIABLE_RUN_STATUSES,
    VerificationService,
)

__all__ = [
    "AgentRuntimeService",
    "ArtifactVerifier",
    "ForkService",
    "ParticipantService",
    "ProfileService",
    "ProviderService",
    "ReplayService",
    "RoleService",
    "ToolDeniedError",
    "ToolDispatcher",
    "ToolDispatchError",
    "ToolRegistry",
    "TranscriptService",
    "pgid_of",
    "DEFAULT_SESSION_POLICIES",
    "ALLOWED_ADAPTER_METHODS",
    "DispatchResult",
    "ReviewService",
    "ReviewServiceError",
    "REPLAY_EQUIVALENCE_NOTE",
    "VERIFIABLE_RUN_STATUSES",
    "VerificationService",
]
