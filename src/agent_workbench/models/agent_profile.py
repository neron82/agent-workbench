"""Agent profile domain model and repository."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentProfile:
    agent_profile_id: str
    name: str
    version: str
    provider_ref: Optional[str]
    model_ref: Optional[str]
    perspective_ref: Optional[str]
    function_ref: Optional[str]
    harness_ref: Optional[str]
    permissions_policy_ref: Optional[str]
    capability_hints_json: Optional[Dict[str, Any]]
    created_at: float
    updated_at: float


class AgentProfileRepository:
    """SQLite-backed repository for AgentProfile entities."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        version: str = "1",
        provider_ref: Optional[str] = None,
        model_ref: Optional[str] = None,
        perspective_ref: Optional[str] = None,
        function_ref: Optional[str] = None,
        harness_ref: Optional[str] = None,
        permissions_policy_ref: Optional[str] = None,
        capability_hints_json: Optional[Dict[str, Any]] = None,
    ) -> AgentProfile:
        """Insert a new agent profile and return the persisted instance."""
        agent_profile_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO agent_profiles ("
            "agent_profile_id, name, version, provider_ref, model_ref, "
            "perspective_ref, function_ref, harness_ref, permissions_policy_ref, "
            "capability_hints_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                agent_profile_id,
                name,
                version,
                provider_ref,
                model_ref,
                perspective_ref,
                function_ref,
                harness_ref,
                permissions_policy_ref,
                json.dumps(capability_hints_json) if capability_hints_json is not None else None,
                now,
                now,
            ),
        )
        self.conn.commit()
        return AgentProfile(
            agent_profile_id=agent_profile_id,
            name=name,
            version=version,
            provider_ref=provider_ref,
            model_ref=model_ref,
            perspective_ref=perspective_ref,
            function_ref=function_ref,
            harness_ref=harness_ref,
            permissions_policy_ref=permissions_policy_ref,
            capability_hints_json=capability_hints_json,
            created_at=now,
            updated_at=now,
        )

    def get_by_id(self, agent_profile_id: str) -> Optional[AgentProfile]:
        row = self.conn.execute(
            "SELECT agent_profile_id, name, version, provider_ref, model_ref, "
            "perspective_ref, function_ref, harness_ref, permissions_policy_ref, "
            "capability_hints_json, created_at, updated_at "
            "FROM agent_profiles WHERE agent_profile_id = ?",
            (agent_profile_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_all(self) -> List[AgentProfile]:
        rows = self.conn.execute(
            "SELECT agent_profile_id, name, version, provider_ref, model_ref, "
            "perspective_ref, function_ref, harness_ref, permissions_policy_ref, "
            "capability_hints_json, created_at, updated_at "
            "FROM agent_profiles ORDER BY updated_at DESC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def update(
        self,
        agent_profile_id: str,
        *,
        name: Optional[str] = None,
        version: Optional[str] = None,
        provider_ref: Optional[str] = None,
        model_ref: Optional[str] = None,
        perspective_ref: Optional[str] = None,
        function_ref: Optional[str] = None,
        harness_ref: Optional[str] = None,
        permissions_policy_ref: Optional[str] = None,
        capability_hints_json: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentProfile]:
        """Create a new version of the profile instead of overwriting.

        Profile updates create new versions (version increments), not
        destructive overwrites. The latest version is returned.
        """
        existing = self.get_by_id(agent_profile_id)
        if existing is None:
            return None

        # Determine the next version string.
        try:
            next_version = str(int(existing.version) + 1)
        except ValueError:
            next_version = "2"

        # Resolve mutable fields: use provided value, fall back to existing.
        new_name = name if name is not None else existing.name
        new_provider_ref = provider_ref if provider_ref is not None else existing.provider_ref
        new_model_ref = model_ref if model_ref is not None else existing.model_ref
        new_perspective_ref = perspective_ref if perspective_ref is not None else existing.perspective_ref
        new_function_ref = function_ref if function_ref is not None else existing.function_ref
        new_harness_ref = harness_ref if harness_ref is not None else existing.harness_ref
        new_permissions_policy_ref = (
            permissions_policy_ref
            if permissions_policy_ref is not None
            else existing.permissions_policy_ref
        )
        new_capability_hints = (
            capability_hints_json
            if capability_hints_json is not None
            else existing.capability_hints_json
        )

        now = time.time()
        new_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO agent_profiles ("
            "agent_profile_id, name, version, provider_ref, model_ref, "
            "perspective_ref, function_ref, harness_ref, permissions_policy_ref, "
            "capability_hints_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id,
                new_name,
                next_version,
                new_provider_ref,
                new_model_ref,
                new_perspective_ref,
                new_function_ref,
                new_harness_ref,
                new_permissions_policy_ref,
                json.dumps(new_capability_hints) if new_capability_hints is not None else None,
                now,
                now,
            ),
        )
        self.conn.commit()
        return AgentProfile(
            agent_profile_id=new_id,
            name=new_name,
            version=next_version,
            provider_ref=new_provider_ref,
            model_ref=new_model_ref,
            perspective_ref=new_perspective_ref,
            function_ref=new_function_ref,
            harness_ref=new_harness_ref,
            permissions_policy_ref=new_permissions_policy_ref,
            capability_hints_json=new_capability_hints,
            created_at=now,
            updated_at=now,
        )

    def delete(self, agent_profile_id: str) -> bool:
        """Delete an agent profile. Returns True if a row was removed."""
        cursor = self.conn.execute(
            "DELETE FROM agent_profiles WHERE agent_profile_id = ?",
            (agent_profile_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_by_name(self, name: str) -> List[AgentProfile]:
        """Return all profile versions matching the given name, newest first."""
        rows = self.conn.execute(
            "SELECT agent_profile_id, name, version, provider_ref, model_ref, "
            "perspective_ref, function_ref, harness_ref, permissions_policy_ref, "
            "capability_hints_json, created_at, updated_at "
            "FROM agent_profiles WHERE name = ? ORDER BY updated_at DESC",
            (name,),
        ).fetchall()
        return [self._row(r) for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> AgentProfile:
        return AgentProfile(
            agent_profile_id=row["agent_profile_id"],
            name=row["name"],
            version=row["version"],
            provider_ref=row["provider_ref"],
            model_ref=row["model_ref"],
            perspective_ref=row["perspective_ref"],
            function_ref=row["function_ref"],
            harness_ref=row["harness_ref"],
            permissions_policy_ref=row["permissions_policy_ref"],
            capability_hints_json=_json_loads(row["capability_hints_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _json_loads(value: Optional[str]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    return json.loads(value)
