"""Runtime helpers for generating agent chat responses.

This is intentionally low-friction: a user message dispatches participant
responses either synchronously (tests) or on a daemon thread (live UI).
Providers currently supported:

* ``mock`` — local deterministic responder for demos and tests
* ``openai_compatible`` — POSTs to ``/chat/completions`` using stdlib HTTP

When the agent profile's harness namespace has registered tools, the
openai_compatible path now performs a tool-calling loop: it sends
``tools=[...]`` to the provider, dispatches any returned ``tool_calls``,
and re-asks the provider with the results.  The loop is capped so a
buggy agent cannot run away.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from agent_workbench.db import apply_migrations, get_connection
from agent_workbench.models.agent_profile import AgentProfileRepository
from agent_workbench.models.provider import ProviderRepository
from agent_workbench.models.routed_message import RoutedMessageRepository
from agent_workbench.models.role import RoleRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.tool import ToolRepository
from agent_workbench.services.agent_status import AgentStatusTracker
from agent_workbench.services.participant_service import ParticipantService
from agent_workbench.services.routing_service import RoutingService
from agent_workbench.services.secret_store import get_secrets_file, resolve_secret
from agent_workbench.services.tool_dispatcher import ToolDispatcher
from agent_workbench.services.tool_registry import (
    DEFAULT_SESSION_POLICIES,
    ToolRegistry,
)


# Hard cap on tool-calling iterations per agent reply.  A buggy or
# malicious agent cannot run forever — once we hit this, we surface
# the partial transcript as the reply.
MAX_TOOL_ITERATIONS = 5


class AgentRuntimeError(RuntimeError):
    """Raised when a configured provider cannot produce a reply."""


class AgentRuntimeService:
    def __init__(self, conn) -> None:
        self.conn = conn
        self.sessions = SessionExtensionRepository(conn)
        self.messages = RoutedMessageRepository(conn)
        self.participants = ParticipantService(conn)
        self.providers = ProviderRepository(conn)
        self.roles = RoleRepository(conn)
        self.profiles = AgentProfileRepository(conn)
        self.routing = RoutingService(conn)
        self.tool_registry = ToolRegistry(ToolRepository(conn))
        self.tool_dispatcher = ToolDispatcher(conn)

    def generate_for_session(
        self,
        *,
        session_id: str,
        user_body: str,
        user_id: str,
    ) -> None:
        session = self.sessions.get_by_id(session_id)
        if session is None:
            raise AgentRuntimeError(f"Session not found: {session_id!r}")
        channel = self.participants.get_channel_for_session(session_id)
        if channel is None:
            raise AgentRuntimeError(f"No channel linked to session {session_id!r}")

        details = self.participants.list_active_participant_details(session_id)
        history = self._build_history(session_id)
        session_type = session.session_type or "chat"
        session_policy = DEFAULT_SESSION_POLICIES.get(
            session_type, ["read_only"]
        )

        for detail in details:
            try:
                reply = self._generate_reply(
                    detail,
                    user_body=user_body,
                    history=history,
                    session=session,
                    session_policy=session_policy,
                    channel=channel,
                )
                payload = json.dumps(
                    {
                        "envelope": "agent_reply",
                        "body": reply,
                        "from": detail["agent_name"],
                        "binding_id": detail["binding_id"],
                        "agent_profile_id": detail["agent_profile_id"],
                    }
                )
                self.routing.route_message(
                    workspace_id=session.workspace_id,
                    channel_id=channel["channel_id"],
                    source_type="agent",
                    source_id=detail["agent_name"],
                    target_type="all",
                    target_id="@all",
                    message_kind="conversation",
                    session_id=session_id,
                    payload_ref=payload,
                )
                history.append({"role": "assistant", "content": reply})
            except Exception as exc:  # pragma: no cover - defensive path exercised via UI smoke
                payload = json.dumps(
                    {
                        "envelope": "agent_error",
                        "body": f"{detail['agent_name']} konnte nicht antworten: {exc}",
                        "from": "system",
                    }
                )
                self.routing.route_message(
                    workspace_id=session.workspace_id,
                    channel_id=channel["channel_id"],
                    source_type="system",
                    source_id="agent-runtime",
                    target_type="all",
                    target_id="@all",
                    message_kind="system",
                    session_id=session_id,
                    payload_ref=payload,
                )

    def _build_history(self, session_id: str, limit: int = 12) -> List[Dict[str, str]]:
        rows = self.messages.list_by_session(session_id)
        visible = [m for m in rows if m.message_kind != "dispatch"][-limit:]
        history: List[Dict[str, str]] = []
        for msg in visible:
            body = extract_message_body(msg.payload_ref)
            if not body:
                continue
            role = "assistant" if msg.source_type in ("agent", "orchestrator", "worker") else "user"
            history.append({"role": role, "content": body})
        return history

    def _generate_reply(
        self,
        detail: Dict[str, Any],
        *,
        user_body: str,
        history: List[Dict[str, str]],
        session: Any,
        session_policy: List[str],
        channel: Optional[Dict[str, Any]] = None,
    ) -> str:
        provider_id = detail.get("provider_ref")
        if not provider_id:
            raise AgentRuntimeError("Agent profile has no provider_ref configured")
        provider = self.providers.get_by_id(provider_id)
        if provider is None:
            raise AgentRuntimeError(f"Provider not found: {provider_id!r}")
        if not provider.is_enabled:
            raise AgentRuntimeError(f"Provider {provider.name!r} is disabled")

        system_prompt = detail.get("role_system_prompt") or (
            "You are a helpful AI assistant inside Agent Workbench. "
            "You have access to tools — use them when you need information "
            "or want to perform an action. You can also reply directly "
            "without calling a tool. When you have enough information to "
            "answer the user, stop calling tools and provide your response."
        )
        if detail.get("perspective_ref"):
            system_prompt += f" Perspective: {detail['perspective_ref']}."
        system_prompt += f" Agent name: {detail['agent_name']}."

        if provider.provider_kind == "mock":
            return self._mock_reply(detail, user_body=user_body, history=history)
        if provider.provider_kind == "openai_compatible":
            # Resolve the full AgentProfile for tool negotiation.
            profile = self.profiles.get_by_id(detail["agent_profile_id"])
            return self._openai_compatible_reply(
                provider=provider,
                detail=detail,
                profile=profile,
                system_prompt=system_prompt,
                history=history,
                user_body=user_body,
                session=session,
                session_policy=session_policy,
                channel=channel,
            )
        raise AgentRuntimeError(f"Unsupported provider kind: {provider.provider_kind!r}")

    @staticmethod
    def _mock_reply(
        detail: Dict[str, Any], *, user_body: str, history: List[Dict[str, str]]
    ) -> str:
        role_name = detail.get("role_name") or detail.get("function_ref") or "assistant"
        prefix = f"[{detail['agent_name']} · {role_name}]"
        history_hint = f" Ich sehe {max(len(history) - 1, 0)} vorherige Chat-Nachricht(en)." if history else ""
        return (
            f"{prefix} Verstanden. Ich habe deine Nachricht erhalten: “{user_body}”."
            f" Das ist eine lokale Mock-Antwort für den UI-Flow.{history_hint}"
        )

    def _openai_compatible_reply(
        self,
        *,
        provider,
        detail: Dict[str, Any],
        profile: Any,
        system_prompt: str,
        history: List[Dict[str, str]],
        user_body: str,
        session: Any,
        session_policy: List[str],
        channel: Optional[Dict[str, Any]] = None,
    ) -> str:
        endpoint = (provider.endpoint_url or "").strip()
        if not endpoint:
            raise AgentRuntimeError(
                f"Provider {provider.name!r} hat keine endpoint_url konfiguriert"
            )
        url = endpoint if endpoint.rstrip("/").endswith("/chat/completions") else endpoint.rstrip("/") + "/chat/completions"
        model = detail.get("model_ref") or provider.default_model
        if not model:
            raise AgentRuntimeError(
                f"Weder Agent {detail['agent_name']!r} noch Provider {provider.name!r} haben ein Modell konfiguriert"
            )
        api_key = None
        if provider.api_key_env_var:
            api_key = resolve_secret(provider.api_key_env_var)
            if not api_key:
                raise AgentRuntimeError(
                    "API-Key-Alias "
                    f"{provider.api_key_env_var!r} ist weder in der Prozess-Umgebung "
                    f"noch in {get_secrets_file()} hinterlegt"
                )

        # Negotiate the effective tool list.
        tools = []
        if profile is not None:
            tools = self.tool_registry.effective_tools(
                agent_profile=profile,
                harness_type=profile.harness_ref,
                session_type=session.session_type or "chat",
                session_policy=session_policy,
            )
        openai_tools = self.tool_registry.to_openai_tools(tools) if tools else None

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(history[-10:])
        if not history or history[-1].get("content") != user_body:
            messages.append({"role": "user", "content": user_body})

        config = provider.config_json or {}
        base_payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": config.get("temperature", 0.4),
        }
        if openai_tools:
            base_payload["tools"] = openai_tools

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        timeout = float(config.get("timeout_seconds", 30))
        # Use session-level max_tool_iterations, fall back to global default
        max_iter = getattr(session, "max_tool_iterations", None) or 5
        agent_name = detail.get("agent_name", "agent")
        tracker = AgentStatusTracker.get_instance()
        tracker.start_agent(session.session_id, agent_name)
        iterations = 0
        final_text = ""
        try:
            while iterations < max_iter:
                if tracker.should_stop(session.session_id, agent_name):
                    final_text = "[agent stopped by user]"
                    break
                iterations += 1
                data = self._post_chat_completions(url, base_payload, headers, timeout)
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                tool_calls = message.get("tool_calls") or []

                if not tool_calls:
                    final_text = (message.get("content") or "").strip()
                    break

                # Append the assistant message that contained the tool_calls,
                # then dispatch each call and append a role=tool result.
                base_payload["messages"].append(message)
                for tc in tool_calls:
                    tc_name = tc.get("function", {}).get("name", "?")
                    tc_args_raw = tc.get("function", {}).get("arguments", "{}")
                    try:
                        tc_args = json.loads(tc_args_raw) if isinstance(tc_args_raw, str) else tc_args_raw
                    except json.JSONDecodeError:
                        tc_args = {"_raw": tc_args_raw}
                    tracker.start_step(
                        session.session_id, agent_name,
                        iteration=iterations,
                        tool_name=tc_name,
                        tool_arguments=tc_args,
                    )
                    result = self.tool_dispatcher.dispatch(
                        session_id=session.session_id,
                        workspace_id=session.workspace_id,
                        session_policy=session_policy,
                        tool_call=tc,
                        agent_harness_type=(
                            profile.harness_ref if profile is not None else None
                        ),
                    )
                    tracker.complete_step(
                        session.session_id, agent_name,
                        result=result.content,
                        failed=(result.status != "completed"),
                    )
                    # Persist work step as a routed_message so it survives
                    # in chat history as a "work done" bubble.
                    if channel:
                        work_payload = json.dumps({
                            "envelope": "agent_work",
                            "agent_name": agent_name,
                            "iteration": iterations,
                            "tool_name": tc_name,
                            "tool_arguments": tc_args,
                            "tool_result": result.content,
                            "status": result.status,
                            "invocation_id": result.invocation_id,
                        })
                        self.routing.route_message(
                            workspace_id=session.workspace_id,
                            channel_id=channel["channel_id"],
                            source_type="agent",
                            source_id=agent_name,
                            target_type="all",
                            target_id="@all",
                            message_kind="agent_work",
                            session_id=session.session_id,
                            payload_ref=work_payload,
                        )
                    if result.status == "pending_confirmation":
                        if channel is not None:
                            self._post_confirmation_request(
                                session=session,
                                channel_id=channel["channel_id"],
                                invocation_id=result.invocation_id,
                                tool_name=result.tool_name,
                                tool_harness_type=result.harness_type,
                                agent_harness_type=profile.harness_ref if profile is not None else None,
                            )
                        base_payload["messages"].append({
                            "role": "tool",
                            "tool_call_id": result.tool_call_id,
                            "content": result.content,
                        })
                    else:
                        base_payload["messages"].append({
                            "role": "tool",
                            "tool_call_id": result.tool_call_id,
                            "content": result.content,
                        })
        except Exception as exc:
            tracker.complete_agent(
                session.session_id, agent_name,
                error=str(exc),
            )
            raise
        finally:
            if not final_text:
                final_text = (
                    f"[agent stopped after {iterations} tool iteration(s) "
                    f"without producing a final reply]"
                )
            tracker.complete_agent(session.session_id, agent_name)
        return final_text

    def _post_confirmation_request(
        self,
        *,
        session: Any,
        channel_id: str,
        invocation_id: str,
        tool_name: str,
        tool_harness_type: str,
        agent_harness_type: Optional[str] = None,
    ) -> None:
        """Post a 'please confirm' message to the channel.

        The message envelope is ``tool_confirmation_request`` so the UI
        can render it with the right styling.  The body includes the
        invocation_id so the confirmation endpoint can find it without
        an extra roundtrip.
        """
        body = json.dumps({
            "envelope": "tool_confirmation_request",
            "invocation_id": invocation_id,
            "tool_name": tool_name,
            "tool_harness_type": tool_harness_type,
            "agent_harness_type": agent_harness_type,
            "options": ["no", "yes_once", "yes_permanent"],
        })
        self.routing.route_message(
            workspace_id=session.workspace_id,
            channel_id=channel_id,
            source_type="system",
            source_id="agent-runtime",
            target_type="all",
            target_id="@all",
            message_kind="tool_confirmation_request",
            session_id=session.session_id,
            payload_ref=body,
        )

    def _post_chat_completions(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        timeout: float,
    ) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise AgentRuntimeError(f"HTTP {exc.code}: {err_body}") from exc
        except urllib.error.URLError as exc:
            raise AgentRuntimeError(str(exc.reason)) from exc


def launch_agent_responses_async(
    *,
    db_path: str,
    session_id: str,
    user_body: str,
    user_id: str,
) -> None:
    thread = threading.Thread(
        target=run_agent_responses,
        kwargs={
            "db_path": db_path,
            "session_id": session_id,
            "user_body": user_body,
            "user_id": user_id,
        },
        daemon=True,
        name=f"agent-workbench-session-{session_id[:8]}",
    )
    thread.start()


def run_agent_responses(
    *,
    db_path: str,
    session_id: str,
    user_body: str,
    user_id: str,
) -> None:
    conn = get_connection(db_path)
    try:
        apply_migrations(conn)
        AgentRuntimeService(conn).generate_for_session(
            session_id=session_id,
            user_body=user_body,
            user_id=user_id,
        )
    finally:
        conn.close()


def extract_message_body(payload_ref: Optional[str]) -> str:
    if not payload_ref:
        return ""
    try:
        data = json.loads(payload_ref)
    except Exception:
        return payload_ref
    if isinstance(data, dict):
        for key in ("body", "message", "content", "text"):
            value = data.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return str(data)
