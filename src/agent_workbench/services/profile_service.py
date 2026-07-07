"""ProfileService — AgentProfile registry and session binding management.

Key invariants:

- ``AgentProfile`` history is append/bind; profile changes during a session
  do not rewrite history, they create a *new* ``AgentProfileBinding``.
- ``change_profile`` therefore creates a new binding with
  ``created_from='profile_change'`` and never mutates the prior binding.
- ``bind_profile`` is the general-purpose creator; orchestrator dispatch
  uses it to attach a profile to a session for work.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from agent_workbench.models.agent_profile import (
    AgentProfile,
    AgentProfileRepository,
)
from agent_workbench.models.agent_profile_binding import (
    AgentProfileBinding,
    AgentProfileBindingRepository,
)
from agent_workbench.models.session_extension import SessionExtensionRepository


_VALID_CREATED_FROM = ("initial", "profile_change", "replay", "retry")


class ProfileNotFoundError(LookupError):
    """Raised when an AgentProfile or binding cannot be found."""


class ProfileService:
    """High-level AgentProfile + binding service."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.profiles = AgentProfileRepository(conn)
        self.bindings = AgentProfileBindingRepository(conn)
        self.sessions = SessionExtensionRepository(conn)

    # ------------------------------------------------------------------
    # Profile registry
    # ------------------------------------------------------------------

    def create_profile(
        self,
        name: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        perspective: Optional[str] = None,
        function: Optional[str] = None,
        harness: Optional[str] = None,
        version: str = "1",
        permissions_policy: Optional[str] = None,
        capability_hints: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> AgentProfile:
        """Create a new AgentProfile.

        The public API uses the friendly field names from the spec
        (provider / model / perspective / function / harness) which map
        to the repository's ``*_ref`` columns. Extra keyword args are
        forwarded to the repository for forward compatibility.
        """
        return self.profiles.create(
            name=name,
            version=version,
            provider_ref=provider,
            model_ref=model,
            perspective_ref=perspective,
            function_ref=function,
            harness_ref=harness,
            permissions_policy_ref=permissions_policy,
            capability_hints_json=capability_hints,
            **kwargs,
        )

    def get_profile(self, agent_profile_id: str) -> AgentProfile:
        """Return the profile or raise :class:`ProfileNotFoundError`."""
        profile = self.profiles.get_by_id(agent_profile_id)
        if profile is None:
            raise ProfileNotFoundError(
                f"AgentProfile not found: {agent_profile_id!r}"
            )
        return profile

    def list_profiles(self) -> List[AgentProfile]:
        return self.profiles.list_all()

    def list_latest_profiles(self) -> List[AgentProfile]:
        """Return profiles deduplicated by name (latest version wins).

        Used by the UI picker to avoid showing every historical version
        of the same agent profile.
        """
        all_profiles = self.profiles.list_all()
        seen: set[str] = set()
        out: List[AgentProfile] = []
        for profile in all_profiles:
            if profile.name in seen:
                continue
            out.append(profile)
            seen.add(profile.name)
        return out

    def get_profiles_by_name(self, name: str) -> List[AgentProfile]:
        return self.profiles.get_by_name(name)

    def update_profile(
        self,
        agent_profile_id: str,
        **fields: Any,
    ) -> AgentProfile:
        """Update via the repository's version-bumping update.

        Map the friendly field names back to the ``*_ref`` repository
        kwargs.
        """
        mapped = self._map_friendly_to_repo_fields(fields)
        updated = self.profiles.update(agent_profile_id, **mapped)
        if updated is None:
            raise ProfileNotFoundError(
                f"AgentProfile not found: {agent_profile_id!r}"
            )
        return updated

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def bind_profile(
        self,
        session_id: str,
        agent_profile_id: str,
        created_from: str = "initial",
        binding_version: str = "1",
    ) -> AgentProfileBinding:
        """Create a new ``AgentProfileBinding`` for a session.

        This always creates a new binding row. It does not modify any
        existing binding for the session.
        """
        if created_from not in _VALID_CREATED_FROM:
            raise ValueError(
                f"Invalid created_from: {created_from!r}. "
                f"Must be one of {_VALID_CREATED_FROM}"
            )
        # Sanity check: session and profile must exist.
        if self.sessions.get_by_id(session_id) is None:
            raise ProfileNotFoundError(f"Session not found: {session_id!r}")
        if self.profiles.get_by_id(agent_profile_id) is None:
            raise ProfileNotFoundError(
                f"AgentProfile not found: {agent_profile_id!r}"
            )

        return self.bindings.create(
            session_id=session_id,
            agent_profile_id=agent_profile_id,
            binding_version=binding_version,
            created_from=created_from,
        )

    def change_profile(
        self,
        session_id: str,
        new_agent_profile_id: str,
    ) -> AgentProfileBinding:
        """Replace the active profile for a session by creating a NEW binding.

        The previous binding (if any) is left untouched — history is
        append-only. The new binding carries ``created_from='profile_change'``.
        """
        return self.bind_profile(
            session_id=session_id,
            agent_profile_id=new_agent_profile_id,
            created_from="profile_change",
        )

    def get_current_binding(
        self, session_id: str
    ) -> Optional[AgentProfileBinding]:
        """Return the most recent binding for the session, or None."""
        return self.bindings.get_latest_for_session(session_id)

    def list_bindings(self, session_id: str) -> List[AgentProfileBinding]:
        """Return all bindings for the session, newest first."""
        return self.bindings.get_by_session(session_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _map_friendly_to_repo_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
        """Map public-friendly field names to repository ``*_ref`` keys."""
        mapping = {
            "name": "name",
            "version": "version",
            "provider": "provider_ref",
            "model": "model_ref",
            "perspective": "perspective_ref",
            "function": "function_ref",
            "harness": "harness_ref",
            "permissions_policy": "permissions_policy_ref",
            "capability_hints": "capability_hints_json",
        }
        out: Dict[str, Any] = {}
        for friendly, repo_key in mapping.items():
            if friendly in fields:
                out[repo_key] = fields[friendly]
        # Forward anything else the caller may have set (e.g. extra kwargs).
        for k, v in fields.items():
            if k not in mapping:
                out[k] = v
        return out
