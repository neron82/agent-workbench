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

import copy
import json
import os
import re
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

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
from agent_workbench.services.tool_registry import ToolRegistry


# Hard cap on tool-calling iterations per agent reply.  A buggy or
# malicious agent cannot run forever — once we hit this, we surface
# the partial transcript as the reply.
MAX_TOOL_ITERATIONS = 5

# Session-scoped lock to prevent overlapping generate_for_session
# calls for the same session.  Uses a nonblocking/bounded approach:
# a second concurrent call for the same session is denied immediately.
_session_locks: Dict[str, threading.Lock] = {}
_session_locks_lock = threading.Lock()


def _get_session_lock(session_id: str) -> threading.Lock:
    """Get or create a nonblocking lock for a session."""
    with _session_locks_lock:
        if session_id not in _session_locks:
            _session_locks[session_id] = threading.Lock()
        return _session_locks[session_id]


def _get_max_concurrent_workers() -> int:
    """Return the max concurrent worker threads per generate_for_session.

    Configurable via ``WORKBENCH_MAX_CONCURRENT_WORKERS`` env var.
    Defaults to 3.
    """
    try:
        return max(1, int(os.environ.get("WORKBENCH_MAX_CONCURRENT_WORKERS", "3")))
    except (ValueError, TypeError):
        return 3


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

    def _parse_agent_mention(self, text: str) -> Optional[str]:
        """Extract an @agent_name from text, if present.

        Looks for ``@word`` at the start or after whitespace.
        Returns the agent name (without ``@``) or ``None``.
        """
        m = re.search(r"(?:^|\s)@(\w[\w-]*)", text)
        if m:
            return m.group(1)
        return None

    def generate_for_session(
        self,
        *,
        session_id: str,
        user_body: str,
        user_id: str,
        target_agent_name: Optional[str] = None,
        target_agent_names: Optional[Sequence[str]] = None,
    ) -> None:
        # Acquire a nonblocking session-scoped lock.  If another
        # generate_for_session is already running for this session,
        # fail immediately — never interleave.
        lock = _get_session_lock(session_id)
        if not lock.acquire(blocking=False):
            raise AgentRuntimeError(
                f"Session {session_id!r} already has a generate_for_session "
                f"in progress; concurrent calls are not allowed"
            )
        try:
            self._generate_for_session_impl(
                session_id=session_id,
                user_body=user_body,
                user_id=user_id,
                target_agent_name=target_agent_name,
                target_agent_names=target_agent_names,
            )
        finally:
            lock.release()

    def _generate_for_session_impl(
        self,
        *,
        session_id: str,
        user_body: str,
        user_id: str,
        target_agent_name: Optional[str] = None,
        target_agent_names: Optional[Sequence[str]] = None,
    ) -> None:
        session = self.sessions.get_by_id(session_id)
        if session is None:
            raise AgentRuntimeError(f"Session not found: {session_id!r}")
        channel = self.participants.get_channel_for_session(session_id)
        if channel is None:
            raise AgentRuntimeError(f"No channel linked to session {session_id!r}")

        details = self.participants.list_active_participant_details(session_id)

        requested_targets = list(target_agent_names or [])
        if target_agent_name and not requested_targets:
            requested_targets = [target_agent_name]
        requested_by_key = {
            name.strip().lower(): name.strip()
            for name in requested_targets
            if name and name.strip()
        }

        # Filter to one or several explicitly selected agents.
        if requested_by_key:
            filtered = [
                d for d in details
                if d["agent_name"].lower() in requested_by_key
            ]
            found = {d["agent_name"].lower() for d in filtered}
            missing = [requested_by_key[key] for key in requested_by_key if key not in found]
            if missing:
                error_msg = (
                    "Agent target(s) not found in this session: "
                    + ", ".join(missing)
                )
                payload = json.dumps({
                    "envelope": "agent_error",
                    "body": error_msg,
                    "from": "system",
                })
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
                return
            details = filtered

        # Build a shared history snapshot that each worker will copy.
        history = self._build_history(session_id)

        # Register every active participant in the status tracker so
        # the status JSON always shows them (idle / queued / working /
        # completed / error / stopped).
        tracker = AgentStatusTracker.get_instance()
        for detail in details:
            tracker.init_agent(session_id, detail["agent_name"])

        # Extract the DB path from this connection so each worker can
        # open its own fresh connection.
        db_path = self.conn.execute("PRAGMA database_list").fetchone()[2]
        if not db_path:
            raise AgentRuntimeError("Cannot determine database path from connection")
        db_path = os.path.abspath(db_path)

        # Session labels are descriptive; no explicit session-level permission
        # policy is configured here. Profile allow/deny hints and negotiated
        # tool names remain authoritative gates.
        # ``None`` is the only unspecified/permissive policy sentinel.
        # An explicit ``[]`` denies all permission classes.
        # Runtime with no explicit session-level policy passes ``None``.
        session_policy: Optional[List[str]] = None

        # Queue all agents, then launch workers with a concurrency cap.
        results: List[Dict[str, Any]] = []
        results_lock = threading.Lock()
        errors: List[Dict[str, Any]] = []
        errors_lock = threading.Lock()
        barrier = threading.Barrier(len(details) + 1, timeout=120)
        concurrency_sem = threading.Semaphore(_get_max_concurrent_workers())

        for detail in details:
            tracker.queue_agent(session_id, detail["agent_name"])

        for detail in details:
            concurrency_sem.acquire()
            worker = threading.Thread(
                target=self._run_worker,
                args=(
                    db_path, session_id, detail, user_body, copy.deepcopy(history),
                    session.workspace_id, channel["channel_id"],
                    getattr(session, "session_type", "chat"),
                    session_policy,
                    results, results_lock,
                    errors, errors_lock,
                    barrier,
                    concurrency_sem,
                ),
                daemon=True,
                name=f"aw-{session_id[:8]}-{detail['agent_name'][:8]}",
            )
            worker.start()

        # Wait for all workers to finish (or timeout).
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass  # Some workers may have errored, but barrier is broken

        # Route all successful replies.
        for r in results:
            payload = json.dumps({
                "envelope": "agent_reply",
                "body": r["reply"],
                "from": r["agent_name"],
                "binding_id": r["binding_id"],
                "agent_profile_id": r["agent_profile_id"],
            })
            try:
                self.routing.route_message(
                    workspace_id=session.workspace_id,
                    channel_id=channel["channel_id"],
                    source_type="agent",
                    source_id=r["agent_name"],
                    target_type="all",
                    target_id="@all",
                    message_kind="conversation",
                    session_id=session_id,
                    payload_ref=payload,
                )
            except Exception:
                pass  # Best-effort routing; one failure doesn't stop others

        # Route all error messages.
        for e in errors:
            payload = json.dumps({
                "envelope": "agent_error",
                "body": f"{e['agent_name']} could not respond: {e['error']}",
                "from": "system",
            })
            try:
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
            except Exception:
                pass

        # ── Iterative auto-turn chain ────────────────────────────────
        # If max_auto_turns > 0, check if any agent's reply contains
        # an @mention of another agent. If so, dispatch only that agent
        # and repeat until max_auto_turns is reached or no @mentions.
        # Rebuild history from the DB to include concurrent replies.
        max_auto_turns = getattr(session, "max_auto_turns", None) or 0
        if max_auto_turns > 0 and not requested_by_key:
            auto_turns_remaining = max_auto_turns
            # Track who spoke last — starts as the last agent in the
            # initial dispatch pass so the self-loop guard works
            # on the first auto-turn iteration too.
            last_agent_name = details[-1]["agent_name"] if details else None
            while auto_turns_remaining > 0:
                history = self._build_history(session_id)
                if not history:
                    break
                last_reply = history[-1]["content"] if history else ""
                next_target = self._parse_agent_mention(last_reply)
                if not next_target:
                    break  # No @mention — chain ends

                # Don't let an agent talk to itself
                if last_agent_name and next_target.lower() == last_agent_name.lower():
                    break

                # Find the target detail
                target_details = [
                    d for d in self.participants.list_active_participant_details(session_id)
                    if d["agent_name"].lower() == next_target.lower()
                ]
                if not target_details:
                    break  # Target not found

                auto_turns_remaining -= 1
                target_detail = target_details[0]
                last_agent_name = target_detail["agent_name"]

                try:
                    reply = self._generate_reply(
                        target_detail,
                        user_body="",
                        history=history,
                        session=session,
                        session_policy=session_policy,
                        channel=channel,
                    )
                    if not reply:
                        # Agent was stopped — skip posting
                        break
                    payload = json.dumps(
                        {
                            "envelope": "agent_reply",
                            "body": reply,
                            "from": target_detail["agent_name"],
                            "binding_id": target_detail["binding_id"],
                            "agent_profile_id": target_detail["agent_profile_id"],
                        }
                    )
                    self.routing.route_message(
                        workspace_id=session.workspace_id,
                        channel_id=channel["channel_id"],
                        source_type="agent",
                        source_id=target_detail["agent_name"],
                        target_type="all",
                        target_id="@all",
                        message_kind="conversation",
                        session_id=session_id,
                        payload_ref=payload,
                    )
                except Exception as exc:
                    payload = json.dumps(
                        {
                            "envelope": "agent_error",
                            "body": f"{target_detail['agent_name']} could not respond: {exc}",
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
                    break

    @staticmethod
    def _run_worker(
        db_path: str,
        session_id: str,
        detail: Dict[str, Any],
        user_body: str,
        history: List[Dict[str, str]],
        workspace_id: str,
        channel_id: str,
        session_type: str,
        session_policy: Optional[List[str]],
        results: List[Dict[str, Any]],
        results_lock: threading.Lock,
        errors: List[Dict[str, Any]],
        errors_lock: threading.Lock,
        barrier: threading.Barrier,
        concurrency_sem: Optional[threading.Semaphore] = None,
    ) -> None:
        """Run a single participant's response in a dedicated worker thread.

        Each worker opens its own fresh SQLite connection so that one
        worker's failure (e.g. DB lock timeout) does not affect others.
        The worker receives an isolated history snapshot so concurrent
        replies don't see partial results from siblings.
        """
        conn = None
        agent_name = detail["agent_name"]
        tracker = AgentStatusTracker.get_instance()
        try:
            conn = get_connection(db_path)
            apply_migrations(conn)

            # Build a fresh runtime service on this worker's connection.
            runtime = AgentRuntimeService(conn)
            tracker.start_agent(session_id, agent_name)

            session = runtime.sessions.get_by_id(session_id)
            if session is None:
                raise AgentRuntimeError(f"Session not found: {session_id!r}")

            channel = runtime.participants.get_channel_for_session(session_id)

            reply = runtime._generate_reply(
                detail,
                user_body=user_body,
                history=history,
                session=session,
                session_policy=session_policy,
                channel=channel,
            )
            if reply:
                with results_lock:
                    results.append({
                        "agent_name": agent_name,
                        "reply": reply,
                        "binding_id": detail["binding_id"],
                        "agent_profile_id": detail["agent_profile_id"],
                    })
                existing = tracker.get_status(session_id, agent_name)
                if existing is None or existing.status not in {"error", "stopped"}:
                    tracker.complete_agent(session_id, agent_name)
            else:
                existing = tracker.get_status(session_id, agent_name)
                if existing is None or existing.status not in {"error", "stopped"}:
                    tracker.complete_agent(session_id, agent_name)
        except Exception as exc:
            with errors_lock:
                errors.append({
                    "agent_name": agent_name,
                    "error": str(exc),
                })
            tracker.complete_agent(session_id, agent_name, error=str(exc))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if concurrency_sem is not None:
                concurrency_sem.release()
            try:
                barrier.wait()
            except (threading.BrokenBarrierError, ValueError):
                pass

    def _build_history(self, session_id: str, limit: int = 12) -> List[Dict[str, str]]:
        rows = self.messages.list_by_session(session_id)
        visible = [m for m in rows if m.message_kind not in ("dispatch", "agent_work")][-limit:]
        history: List[Dict[str, str]] = []
        for msg in visible:
            body = extract_message_body(msg.payload_ref)
            if not body:
                continue
            role = "assistant" if msg.source_type in ("agent", "orchestrator", "worker") else "user"
            entry: Dict[str, str] = {"role": role, "content": body}
            # Include the agent name so the LLM can distinguish who said what
            if role == "assistant" and msg.source_id:
                entry["name"] = msg.source_id
            history.append(entry)
        return history

    def _generate_reply(
        self,
        detail: Dict[str, Any],
        *,
        user_body: str,
        history: List[Dict[str, str]],
        session: Any,
        session_policy: Optional[List[str]],
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

        # List other participants so the agent can @mention them
        # (re-fetch from DB to get all active participants, not just the target)
        all_participants = self.participants.list_active_participant_details(
            session.session_id
        )
        other_names = [
            p["agent_name"]
            for p in all_participants
            if p["agent_name"] != detail["agent_name"]
        ]
        if other_names:
            system_prompt += (
                " Other participants in this session: "
                + ", ".join(f"@{n}" for n in other_names)
                + ". "
                "You can ask them for help by writing @their_name in your reply. "
                "They will see your message and can respond automatically."
            )

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
        session_policy: Optional[List[str]],
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
        work_steps: List[Dict[str, Any]] = []
        had_error = False
        try:
            while iterations < max_iter:
                if tracker.should_stop(session.session_id, agent_name):
                    # Post a system message so the UI shows the stop clearly
                    if channel is not None:
                        stop_payload = json.dumps({
                            "envelope": "system_notice",
                            "body": f"Agent {agent_name} was stopped by user.",
                            "from": "system",
                        })
                        self.routing.route_message(
                            workspace_id=session.workspace_id,
                            channel_id=channel["channel_id"],
                            source_type="system",
                            source_id="agent-runtime",
                            target_type="all",
                            target_id="@all",
                            message_kind="system",
                            session_id=session.session_id,
                            payload_ref=stop_payload,
                        )
                    final_text = ""
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
                        # Runtime calls are never trusted internal calls.  An
                        # explicitly empty negotiated set must therefore stay
                        # empty rather than becoming ``None`` (the dispatcher's
                        # backwards-compatible unrestricted sentinel).
                        allowed_tool_names=[
                            t["function"]["name"] for t in (openai_tools or [])
                        ],
                    )
                    tracker.complete_step(
                        session.session_id, agent_name,
                        result=result.content,
                        failed=(result.status != "completed"),
                    )
                    # Collect work steps for batch posting after agent is done
                    work_steps.append({
                        "iteration": iterations,
                        "tool_name": tc_name,
                        "tool_arguments": tc_args,
                        "tool_result": result.content,
                        "status": result.status,
                        "invocation_id": result.invocation_id,
                    })
                    if result.status == "pending_confirmation":
                        if channel is not None:
                            self._post_confirmation_request(
                                session=session,
                                channel_id=channel["channel_id"],
                                invocation_id=result.invocation_id or "",
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
            had_error = True
            tracker.complete_agent(
                session.session_id, agent_name,
                error=str(exc),
            )
            raise
        finally:
            if not final_text and iterations > 0:
                final_text = (
                    f"[agent stopped after {iterations} tool iteration(s) "
                    f"without producing a final reply]"
                )
            # Batch-post collected work steps as a single agent_work message
            if work_steps and channel:
                work_payload = json.dumps({
                    "envelope": "agent_work",
                    "agent_name": agent_name,
                    "steps": work_steps,
                    "status": "completed" if not final_text.startswith("[agent stopped") else "stopped",
                })
                try:
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
                except Exception:
                    # Work telemetry must not overwrite the actual agent state.
                    pass
            if not had_error:
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
            reason = str(exc.reason)
            if "timed out" in reason.lower():
                raise AgentRuntimeError(
                    f"LLM antwortete nicht innerhalb von {timeout:.0f}s (Timeout). "
                    f"Das Modell braucht möglicherweise länger für diese Anfrage. "
                    f"Erhöhe timeout_seconds im Provider oder verkürze die Eingabe."
                ) from exc
            raise AgentRuntimeError(
                f"Verbindung zum LLM-Endpunkt fehlgeschlagen: {reason}"
            ) from exc


def launch_agent_responses_async(
    *,
    db_path: str,
    session_id: str,
    user_body: str,
    user_id: str,
    target_agent_name: Optional[str] = None,
    target_agent_names: Optional[Sequence[str]] = None,
) -> None:
    thread = threading.Thread(
        target=run_agent_responses,
        kwargs={
            "db_path": db_path,
            "session_id": session_id,
            "user_body": user_body,
            "user_id": user_id,
            "target_agent_name": target_agent_name,
            "target_agent_names": target_agent_names,
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
    target_agent_name: Optional[str] = None,
    target_agent_names: Optional[Sequence[str]] = None,
) -> None:
    conn = get_connection(db_path)
    try:
        apply_migrations(conn)
        AgentRuntimeService(conn).generate_for_session(
            session_id=session_id,
            user_body=user_body,
            user_id=user_id,
            target_agent_name=target_agent_name,
            target_agent_names=target_agent_names,
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
