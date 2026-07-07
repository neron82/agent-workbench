"""ToolDispatcher — execute a tool_call emitted by a provider.

Given a single ``tool_call`` (in OpenAI's format) plus the AgentProfile
and session context, the dispatcher:

1. Looks up the Tool by ``(harness_type, name)``.
2. Checks that the tool is allowed for this session policy.
3. Creates a ToolInvocation record (status=running).
4. Calls the corresponding adapter method, passing the parsed arguments.
5. Updates the ToolInvocation with status, result, error, harness_run_id.
6. Returns a dict shaped for the OpenAI ``role=tool`` message.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.tool import Tool
from agent_workbench.models.tool_invocation import (
    ToolInvocationRepository,
)


# Methods a tool's ``adapter_method`` may call.  Anything outside this
# allow-list is rejected at dispatch time so a poisoned tool catalog
# can't trigger arbitrary adapter methods.
ALLOWED_ADAPTER_METHODS = frozenset({
    "start",            # local shell / ssh / hermes
    "execute_shell",    # hermes
    "write_file",       # hermes
    "delegate_subagent",  # hermes (stub for now)
})


def max_output_chars() -> int:
    """Return the max characters to include from tool stdout in the result.

    Configurable via ``WORKBENCH_TOOL_OUTPUT_MAX_CHARS`` env var.
    Defaults to 4000.
    """
    try:
        return int(os.environ.get("WORKBENCH_TOOL_OUTPUT_MAX_CHARS", "4000"))
    except (ValueError, TypeError):
        return 4000


class ToolDispatchError(Exception):
    """Raised when a tool call cannot be dispatched."""


class ToolDeniedError(ToolDispatchError):
    """Raised when a session policy denies a tool call."""


@dataclass
class DispatchResult:
    """Result of dispatching one tool call."""

    tool_call_id: str
    tool_name: str         # namespaced: "{harness_type}.{name}"
    harness_type: str
    status: str           # "completed" | "failed" | "denied" | "pending_confirmation"
    content: str          # string content to send back as role=tool
    invocation_id: Optional[str] = None
    harness_run_id: Optional[str] = None


def extract_agent_harness_from_reason(reason: str) -> Optional[str]:
    """Pull the agent's harness type out of a confirmation_reason string.

    The reason we stored in ``tool_invocations.confirmation_reason`` has
    the form:
        "Tool 'hermes.run_command' is outside the agent's configured
         harness 'hermes'; user must confirm."

    We want the second quoted identifier — that's the agent's harness.
    """
    if "configured harness " in reason:
        tail = reason.split("configured harness ", 1)[1]
        if tail.startswith("'"):
            tail = tail[1:]
        for stop in ("'", ";", " "):
            if stop in tail:
                tail = tail.split(stop, 1)[0]
                break
        return tail.strip() or None
    return None


def reconstruct_tool_call(invocation: Any) -> Dict[str, Any]:
    """Reconstruct a provider-style tool_call envelope from a stored
    ToolInvocation so the dispatcher can re-execute it.

    The original ``tool_call_id`` is replaced with a deterministic
    replay-id — the new dispatch returns a fresh ``invocation_id`` for
    the result message.
    """
    return {
        "id": f"replay_{invocation.invocation_id[:12]}",
        "function": {
            "name": (
                f"{invocation.tool_harness_type}.{invocation.tool_name}"
                if invocation.tool_harness_type
                else invocation.tool_name
            ),
            "arguments": json.dumps(
                invocation.arguments_json or {}, sort_keys=True
            ),
        },
    }


class ToolDispatcher:
    """Dispatches tool calls to the right adapter method."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        from agent_workbench.models.cross_harness_permission import (
            CrossHarnessPermissionRepository,
        )
        from agent_workbench.models.tool import ToolRepository
        self._tool_repo = ToolRepository(conn)
        self._invocations = ToolInvocationRepository(conn)
        self._harness_repo = HarnessRunRepository(conn)
        self._cross_perms = CrossHarnessPermissionRepository(conn)

    def dispatch(
        self,
        *,
        session_id: str,
        workspace_id: str,
        session_policy: List[str],
        tool_call: Dict[str, Any],
        agent_harness_type: Optional[str] = None,
    ) -> DispatchResult:
        """Execute one tool_call.  Returns a :class:`DispatchResult`."""
        raw_name = tool_call.get("function", {}).get("name", "")
        raw_args = tool_call.get("function", {}).get("arguments", "{}")
        call_id = tool_call.get("id", "")

        # Strip harness_type namespace; the catalog uses the short name.
        if "." in raw_name:
            harness_type, name = raw_name.split(".", 1)
        else:
            harness_type, name = "", raw_name

        if not harness_type or not name:
            return self._denied(
                call_id, raw_name, harness_type, name,
                reason=f"Malformed tool_call name: {raw_name!r}",
            )

        tool = self._tool_repo.get_by_name(harness_type, name)
        if tool is None or not tool.is_enabled:
            return self._denied(
                call_id, raw_name, harness_type, name,
                reason=f"Tool {raw_name!r} is not registered or disabled",
            )

        if tool.permission_class not in set(session_policy):
            return self._denied(
                call_id, raw_name, harness_type, name,
                reason=(
                    f"Tool {raw_name!r} requires permission "
                    f"{tool.permission_class!r} which is not in this "
                    f"session's policy {session_policy!r}"
                ),
                tool=tool,
                session_id=session_id,
                workspace_id=workspace_id,
            )

        if tool.adapter_method not in ALLOWED_ADAPTER_METHODS:
            return self._denied(
                call_id, raw_name, harness_type, name,
                reason=(
                    f"Tool {raw_name!r} declares adapter_method "
                    f"{tool.adapter_method!r} which is not in the "
                    f"dispatcher allow-list"
                ),
                tool=tool,
                session_id=session_id,
                workspace_id=workspace_id,
            )

        # Cross-harness check: if the agent's configured harness is set
        # and differs from the tool's harness, we need user approval.
        # No-op when ``agent_harness_type`` is None (caller didn't tell
        # us, or the agent has no harness configured).
        if (
            agent_harness_type
            and agent_harness_type != harness_type
            and not self._cross_perms.is_allowed(
                session_id=session_id,
                agent_harness_type=agent_harness_type,
                tool_harness_type=harness_type,
            )
        ):
            return self._pending_confirmation(
                call_id=call_id,
                raw_name=raw_name,
                harness_type=harness_type,
                name=name,
                tool=tool,
                session_id=session_id,
                workspace_id=workspace_id,
                agent_harness_type=agent_harness_type,
                raw_args=raw_args,
            )

        # Parse arguments as JSON.  If they fail, the call is malformed
        # from the provider's side and we mark it failed (not denied).
        try:
            if isinstance(raw_args, str):
                arguments = json.loads(raw_args) if raw_args.strip() else {}
            elif isinstance(raw_args, dict):
                arguments = raw_args
            else:
                arguments = {}
        except json.JSONDecodeError as exc:
            inv = self._invocations.create(
                session_id=session_id,
                workspace_id=workspace_id,
                tool_id=tool.tool_id,
                tool_name=tool.name,
                arguments={"_raw": raw_args},
                status="failed",
            )
            self._invocations.update_status(
                inv.invocation_id,
                status="failed",
                error_text=f"arguments are not valid JSON: {exc}",
            )
            return DispatchResult(
                tool_call_id=call_id,
                tool_name=raw_name,
                harness_type=harness_type,
                status="failed",
                content=json.dumps({
                    "ok": False,
                    "error": f"arguments are not valid JSON: {exc}",
                }),
                invocation_id=inv.invocation_id,
            )

        # Create a pending invocation and call the adapter.
        invocation = self._invocations.create(
            session_id=session_id,
            workspace_id=workspace_id,
            tool_id=tool.tool_id,
            tool_name=tool.name,
            arguments=arguments,
            status="running",
        )

        result_content, run_id, error = self._call_adapter(
            tool=tool,
            session_id=session_id,
            arguments=arguments,
            invocation_id=invocation.invocation_id,
        )

        if error is not None:
            self._invocations.update_status(
                invocation.invocation_id,
                status="failed",
                error_text=error,
                harness_run_id=run_id,
            )
            return DispatchResult(
                tool_call_id=call_id,
                tool_name=raw_name,
                harness_type=harness_type,
                status="failed",
                content=result_content,
                invocation_id=invocation.invocation_id,
                harness_run_id=run_id,
            )

        self._invocations.update_status(
            invocation.invocation_id,
            status="completed",
            result_text=result_content,
            harness_run_id=run_id,
        )
        return DispatchResult(
            tool_call_id=call_id,
            tool_name=raw_name,
            harness_type=harness_type,
            status="completed",
            content=result_content,
            invocation_id=invocation.invocation_id,
            harness_run_id=run_id,
        )

    # ------------------------------------------------------------------
    # Internal: adapter call
    # ------------------------------------------------------------------

    def _call_adapter(
        self,
        *,
        tool: Tool,
        session_id: str,
        arguments: Dict[str, Any],
        invocation_id: Optional[str] = None,
    ) -> Tuple[str, Optional[str], Optional[str]]:
        """Invoke the adapter method, return (content, harness_run_id, error).

        When ``invocation_id`` is provided, the resulting HarnessRun is
        linked back to the invocation so the run detail page can show
        "triggered by tool_call" and the UI can navigate from invocation
        to run.
        """
        # Local import to avoid a circular import: hermes_adapter.py
        # imports from agent_workbench.services at module load.
        from agent_workbench.adapters import get_adapter_class
        adapter_cls = get_adapter_class(tool.harness_type)
        if adapter_cls is None:
            return (
                json.dumps({"ok": False, "error": f"unknown harness: {tool.harness_type!r}"}),
                None,
                f"unknown harness_type: {tool.harness_type!r}",
            )
        try:
            adapter = adapter_cls(self.conn)
        except Exception as exc:
            return (
                json.dumps({"ok": False, "error": f"adapter init failed: {exc}"}),
                None,
                str(exc),
            )

        try:
            if tool.adapter_method == "start":
                # Most common path: spawn a local command.
                command = arguments.get("command", "")
                if not command:
                    return (
                        json.dumps({"ok": False, "error": "missing 'command' argument"}),
                        None,
                        "missing 'command'",
                    )
                run_id = adapter.start(
                    workspace_id=self._resolve_workspace(session_id),
                    session_id=session_id,
                    command=command,
                )
                self._link_invocation_to_run(run_id, invocation_id)
                # Best-effort: collect the transcript after a brief
                # delay so we have a meaningful result.  We do *not*
                # block forever; the agent will see whatever is
                # available at the moment of return.
                deadline = time.time() + float(
                    __import__("os").environ.get("WORKBENCH_TOOL_COLLECT_TIMEOUT", "3")
                )
                stdout = ""
                while time.time() < deadline:
                    transcript = adapter.get_transcript(run_id)
                    stdout = (transcript.stdout or "").strip()
                    run = self._harness_repo.get_by_id(run_id)
                    if run and run.status in ("completed", "failed", "cancelled"):
                        break
                    if stdout:
                        break
                    time.sleep(0.1)
                payload = {
                    "ok": True,
                    "harness_run_id": run_id,
                    "command": command,
                    "stdout": stdout[:max_output_chars()],
                }
                return json.dumps(payload, ensure_ascii=False), run_id, None

            if tool.adapter_method == "execute_shell":
                # Hermes: attach to an existing session, or auto-spawn one.
                run_id = arguments.get("harness_run_id")
                if not run_id:
                    # Auto-spawn a Hermes session so the agent can use
                    # this tool without first having to start a session
                    # manually.  We give the session a benign bootstrap
                    # command; the real work follows in execute_shell.
                    run_id = self._auto_spawn(
                        adapter=adapter,
                        session_id=session_id,
                        bootstrap="true",  # cheap placeholder
                    )
                command = arguments.get("command", "")
                try:
                    transcript = adapter.execute_shell(run_id, command=command)
                except Exception as exc:
                    if tool.harness_type != "hermes" or not self._is_missing_hermes_session(exc):
                        raise
                    run_id = self._auto_spawn(
                        adapter=adapter,
                        session_id=session_id,
                        bootstrap="true",
                    )
                    transcript = adapter.execute_shell(run_id, command=command)
                payload = {
                    "ok": True,
                    "harness_run_id": run_id,
                    "stdout": (transcript.stdout or "").strip()[:4000],
                }
                self._link_invocation_to_run(run_id, invocation_id)
                return json.dumps(payload, ensure_ascii=False), run_id, None

            if tool.adapter_method == "write_file":
                # Hermes: attach to an existing session, or auto-spawn one.
                run_id = arguments.get("harness_run_id")
                if not run_id:
                    run_id = self._auto_spawn(
                        adapter=adapter,
                        session_id=session_id,
                        bootstrap="true",
                    )
                path = arguments.get("path", "")
                data = arguments.get("data", "")
                if not path:
                    return (
                        json.dumps({"ok": False, "error": "missing 'path' argument"}),
                        None,
                        "missing path",
                    )
                try:
                    returned = adapter.write_file(run_id, path=path, data=data)
                except Exception as exc:
                    if tool.harness_type != "hermes" or not self._is_missing_hermes_session(exc):
                        raise
                    run_id = self._auto_spawn(
                        adapter=adapter,
                        session_id=session_id,
                        bootstrap="true",
                    )
                    returned = adapter.write_file(run_id, path=path, data=data)
                payload = {
                    "ok": True,
                    "harness_run_id": run_id,
                    "path": returned,
                }
                self._link_invocation_to_run(run_id, invocation_id)
                return json.dumps(payload, ensure_ascii=False), run_id, None

            if tool.adapter_method == "delegate_subagent":
                # Hermes stub: we *do not* fake this.  Return a precise
                # error so the agent knows the product layer doesn't
                # implement it yet.  Future PR can wire it up.
                return (
                    json.dumps({
                        "ok": False,
                        "error": (
                            "hermes.delegate_subagent is not yet implemented "
                            "in the product layer; the agent must finish "
                            "the task without subagent delegation."
                        ),
                    }),
                    None,
                    "not implemented",
                )

            return (
                json.dumps({"ok": False, "error": f"unhandled adapter_method: {tool.adapter_method!r}"}),
                None,
                f"unhandled adapter_method: {tool.adapter_method!r}",
            )
        except Exception as exc:
            return (
                json.dumps({"ok": False, "error": f"adapter raised: {exc}"}),
                None,
                str(exc),
            )

    def _resolve_workspace(self, session_id: str) -> str:
        from agent_workbench.models.session_extension import (
            SessionExtensionRepository,
        )
        sess = SessionExtensionRepository(self.conn).get_by_id(session_id)
        if sess is None:
            raise ToolDispatchError(f"Session not found: {session_id!r}")
        return sess.workspace_id

    def _link_invocation_to_run(
        self, run_id: Optional[str], invocation_id: Optional[str]
    ) -> None:
        """Best-effort back-link from a HarnessRun to its ToolInvocation.

        We swallow any error so a back-link failure can never break the
        dispatch path.  The run is the durable thing — a missing
        back-link is a UI cosmetic, not a correctness issue.
        """
        if not (run_id and invocation_id):
            return
        try:
            self.conn.execute(
                "UPDATE harness_runs SET tool_invocation_id = ? "
                "WHERE harness_run_id = ?",
                (invocation_id, run_id),
            )
            self.conn.commit()
        except Exception:
            pass

    def _auto_spawn(
        self,
        *,
        adapter: Any,
        session_id: str,
        bootstrap: str,
    ) -> str:
        """Start a fresh adapter session and return its harness_run_id.

        Used by hermes.* tools that previously required an explicit
        ``harness_run_id``.  Now they can ask the dispatcher to spin up
        a placeholder session, then continue with the real work.
        """
        return adapter.start(
            workspace_id=self._resolve_workspace(session_id),
            session_id=session_id,
            command=bootstrap,
        )

    @staticmethod
    def _is_missing_hermes_session(exc: Exception) -> bool:
        text = str(exc)
        return "No Hermes session for " in text

    def _denied(
        self,
        call_id: str,
        raw_name: str,
        harness_type: str,
        name: str,
        *,
        reason: str,
        tool: Optional[Tool] = None,
        session_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> DispatchResult:
        invocation_id: Optional[str] = None
        if tool is not None and session_id and workspace_id:
            inv = self._invocations.create(
                session_id=session_id,
                workspace_id=workspace_id,
                tool_id=tool.tool_id,
                tool_name=tool.name,
                status="denied",
            )
            self._invocations.update_status(
                inv.invocation_id, status="denied", error_text=reason,
            )
            invocation_id = inv.invocation_id
        return DispatchResult(
            tool_call_id=call_id,
            tool_name=raw_name,
            harness_type=harness_type or "",
            status="denied",
            content=json.dumps({"ok": False, "error": reason}),
            invocation_id=invocation_id,
        )

    def _pending_confirmation(
        self,
        *,
        call_id: str,
        raw_name: str,
        harness_type: str,
        name: str,
        tool: Tool,
        session_id: str,
        workspace_id: str,
        agent_harness_type: str,
        raw_args: Any = None,
    ) -> DispatchResult:
        """Create a pending-confirmation invocation and return a stub result.

        The caller is responsible for posting a confirmation message
        to the channel (this method does NOT do that — it only persists
        the invocation so the UI can find it).
        """
        # Best-effort parse so we can store the arguments for later
        # replay.  Malformed JSON just means we replay with empty
        # arguments; the tool will fail again on its own.
        if isinstance(raw_args, str):
            try:
                arguments = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                arguments = {"_raw": raw_args}
        elif isinstance(raw_args, dict):
            arguments = raw_args
        else:
            arguments = {}

        reason = (
            f"Tool {raw_name!r} is outside the agent's configured "
            f"harness {agent_harness_type!r}; user must confirm."
        )
        inv = self._invocations.create(
            session_id=session_id,
            workspace_id=workspace_id,
            tool_id=tool.tool_id,
            tool_name=tool.name,
            tool_harness_type=harness_type,
            arguments=arguments,
            status="pending_confirmation",
            requires_confirmation=True,
            confirmation_reason=reason,
        )
        return DispatchResult(
            tool_call_id=call_id,
            tool_name=raw_name,
            harness_type=harness_type,
            status="pending_confirmation",
            content=json.dumps({
                "ok": False,
                "error": (
                    f"Tool {raw_name!r} requires user confirmation. "
                    f"It belongs to harness {harness_type!r} but the agent "
                    f"is configured for {agent_harness_type!r}."
                ),
                "invocation_id": inv.invocation_id,
            }),
            invocation_id=inv.invocation_id,
        )
