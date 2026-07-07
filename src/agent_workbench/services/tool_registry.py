"""ToolRegistry — negotiate the effective tool list for an agent.

This is the single source of truth for "which tools does this agent
actually get to call?".  Per the spec (05_AGENT_PROFILES §5 and
open_decisions.md decision 10), the effective toolset is the
intersection of three filters:

1. ``AgentProfile.capability_hints`` — the agent's own preferences
   (e.g. allowed tool names, allowed harness types)
2. ``HarnessCapabilities`` — what the underlying adapter actually
   supports (e.g. ``can_shell=True`` for ShellAdapter)
3. ``SessionPolicy`` — runtime gate (e.g. work session allows
   ``destructive`` tools, chat session does not)

If no profile hints or session policy are set, the registry falls
back to all enabled tools for the resolved harness type.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent_workbench.models.tool import Tool, ToolRepository


# Default session policies by session_type.  ``chat`` is restrictive,
# ``research`` allows read tools, ``work`` allows everything.
DEFAULT_SESSION_POLICIES: Dict[str, List[str]] = {
    "chat":     ["read_only"],
    "research": ["read_only", "write_local"],
    "work":     ["read_only", "write_local", "write_remote", "destructive"],
}


class ToolRegistry:
    """Negotiate and shape the effective tool list for one agent call."""

    def __init__(self, tool_repo: ToolRepository) -> None:
        self.tools = tool_repo

    # ------------------------------------------------------------------
    # Negotiation
    # ------------------------------------------------------------------

    def effective_tools(
        self,
        *,
        agent_profile: Any,
        harness_type: Optional[str],
        session_type: str = "chat",
        session_policy: Optional[List[str]] = None,
    ) -> List[Tool]:
        """Return the tools this agent may call right now.

        Parameters
        ----------
        agent_profile:
            An ``AgentProfile`` instance (only ``capability_hints_json``
            and ``harness_ref`` are read).
        harness_type:
            The harness namespace to pull tools from.  Falls back to
            ``agent_profile.harness_ref`` if not given.
        session_type:
            ``chat | research | work`` — used to pick the default
            permission policy when ``session_policy`` is None.
        session_policy:
            Explicit allow-list of ``permission_class`` values.  Overrides
            the default for the session type.
        """
        ht = harness_type or getattr(agent_profile, "harness_ref", None)
        if not ht:
            return []

        # 1) Adapter capability filter — local import to break the
        # adapters <-> services import cycle.
        from agent_workbench.adapters import get_adapter_class
        from agent_workbench.adapters.base import AdapterCapabilities
        adapter_cls = get_adapter_class(ht)
        if adapter_cls is None:
            return []
        adapter_caps: AdapterCapabilities = adapter_cls.capabilities
        if not self._harness_supports_tools(adapter_caps):
            return []

        # 2) All enabled tools for this harness
        candidates = self.tools.list_for_harness(ht)

        # 3) Agent profile capability_hints filter
        hints = getattr(agent_profile, "capability_hints_json", None) or {}
        allowed_names = hints.get("allowed_tools")
        denied_names = set(hints.get("denied_tools") or [])

        # 4) Session permission_class allow-list
        policy = session_policy or DEFAULT_SESSION_POLICIES.get(
            session_type, ["read_only"]
        )
        policy_set = set(policy)

        out: List[Tool] = []
        for t in candidates:
            if allowed_names is not None and t.name not in allowed_names:
                continue
            if t.name in denied_names:
                continue
            if t.permission_class not in policy_set:
                continue
            out.append(t)
        return out

    @staticmethod
    def _harness_supports_tools(caps: AdapterCapabilities) -> bool:
        """A harness can expose tools if it can run *something* on the agent's
        behalf.  Right now this is a permissive ``True`` for every adapter
        except discussion (which has no side effects) and a no-op
        ``can_*`` set.  We can tighten this later when the per-harness
        tool catalog stabilises."""
        if not any((
            caps.can_shell, caps.can_file_write, caps.can_remote,
        )):
            return False
        return True

    # ------------------------------------------------------------------
    # OpenAI schema builder
    # ------------------------------------------------------------------

    def to_openai_tools(self, tools: List[Tool]) -> List[Dict[str, Any]]:
        """Render the negotiated tools in OpenAI's ``function`` shape.

        The result is a list of ``{"type": "function", "function": {...}}``
        objects suitable for the ``tools`` parameter of
        ``/v1/chat/completions``.

        Tool names are namespaced as ``{harness_type}.{name}`` to avoid
        collisions between harnesses that happen to register the same
        short name (e.g. ``shell.run`` vs ``hermes.run``).
        """
        out: List[Dict[str, Any]] = []
        for t in tools:
            out.append({
                "type": "function",
                "function": {
                    "name": f"{t.harness_type}.{t.name}",
                    "description": t.description or f"Run {t.name} on {t.harness_type}",
                    "parameters": t.input_schema_json or {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            })
        return out
