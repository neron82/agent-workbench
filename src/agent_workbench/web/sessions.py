"""Sessions blueprint — session detail, chat participation, and message post."""

from __future__ import annotations

import json
from typing import Optional

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from agent_workbench.models.agent_profile_binding import AgentProfileBindingRepository
from agent_workbench.models.channel import ChannelRepository
from agent_workbench.models.harness_run import HarnessRunRepository
from agent_workbench.models.session_extension import (
    SESSION_STATUSES,
    SessionExtensionRepository,
)
from agent_workbench.models.task_spec import TaskSpecRepository
from agent_workbench.services.agent_runtime_service import (
    AgentRuntimeService,
    launch_agent_responses_async,
)
from agent_workbench.services.profile_service import ProfileNotFoundError, ProfileService
from agent_workbench.services.routing_service import (
    RoutingService,
    SOURCE_TYPE_USER,
    TARGET_TYPE_ORCHESTRATOR,
)
from agent_workbench.services.run_service import RunService
from agent_workbench.services.session_service import (
    SessionNotFoundError,
    SessionService,
)
from agent_workbench.web.app import (
    get_db,
    get_orchestrator,
    get_participant_service,
    get_routing_service,
    get_session_service,
)
from agent_workbench.web.messages import visible_messages_for_session

bp = Blueprint("sessions", __name__)


def _get_session_or_404(session_id: str):
    repo = SessionExtensionRepository(get_db())
    sess = repo.get_by_id(session_id)
    if sess is None:
        abort(404, description=f"Session {session_id!r} not found")
    return sess


def _resolve_channel_for_session(session) -> Optional[object]:
    """Return the channel whose ``active_session_id`` matches *session*.

    Uses a direct SQL query instead of iterating all channels in the
    workspace (O(1) vs O(n)).
    """
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM channels WHERE active_session_id = ? LIMIT 1",
        (session.session_id,),
    ).fetchone()
    if row is None:
        return None
    from agent_workbench.models.channel import Channel
    return Channel(**dict(row))


def _resolve_binding(session) -> Optional[object]:
    repo = AgentProfileBindingRepository(get_db())
    return repo.get_latest_for_session(session.session_id)


def _latest_profiles_for_picker() -> list:
    return ProfileService(get_db()).list_latest_profiles()


@bp.route("/sessions/<session_id>/config", methods=["GET"])
def session_config(session_id: str):
    """Session configuration page — title, workspace info, delete."""
    session = _get_session_or_404(session_id)
    channel = _resolve_channel_for_session(session)
    participants = get_participant_service().list_active_participant_details(session_id)

    # Resolve workspace name
    from agent_workbench.models.workspace import WorkspaceRepository
    ws = WorkspaceRepository(get_db()).get_by_id(session.workspace_id)

    return render_template(
        "session_config.html",
        session=session,
        channel=channel,
        participants=participants,
        workspace=ws,
    )


@bp.route("/sessions/<session_id>", methods=["GET"])
def show_session(session_id: str):
    session = _get_session_or_404(session_id)
    binding = _resolve_binding(session)
    channel = _resolve_channel_for_session(session)
    is_work = session.session_type == "work"
    participants = get_participant_service().list_active_participant_details(session_id)
    messages = visible_messages_for_session(session_id)

    # Run-Liste + Start-Picker (aditive UI-Schicht).
    run_service = RunService(get_db())
    session_runs = run_service.list_for_session(session_id)
    available_harness_types = run_service.available_harness_types()

    # TaskSpec-Picker: alle approved Specs des Workspaces (work-Sessions)
    # bzw. alle Specs (chat/research) für ad-hoc-Bindung.
    spec_repo = TaskSpecRepository(get_db())
    workspace_specs = spec_repo.list_by_workspace(session.workspace_id)
    if is_work:
        eligible_specs = [s for s in workspace_specs if s.approval_status == "approved"]
    else:
        eligible_specs = workspace_specs

    # Tool-call confirmation messages
    from agent_workbench.models.tool_invocation import ToolInvocationRepository
    inv_repo = ToolInvocationRepository(get_db())
    pending_invocation_ids = {
        inv.invocation_id
        for inv in inv_repo.list_pending_confirmation(session_id)
    }

    return render_template(
        "session_view.html",
        session=session,
        session_id=session_id,
        messages=messages,
        binding=binding,
        channel=channel,
        is_work=is_work,
        participants=participants,
        available_agents=_latest_profiles_for_picker(),
        session_statuses=SESSION_STATUSES,
        session_runs=session_runs,
        available_harness_types=available_harness_types,
        eligible_specs=eligible_specs,
        pending_invocation_ids=pending_invocation_ids,
    )


@bp.route("/sessions/<session_id>/participants", methods=["POST"])
def add_participant(session_id: str):
    _get_session_or_404(session_id)
    agent_profile_id = (request.form.get("agent_profile_id") or "").strip()
    if not agent_profile_id:
        flash("Bitte einen Agenten auswählen.", "error")
        return redirect(url_for("sessions.show_session", session_id=session_id))
    participant_role = (request.form.get("participant_role") or "member").strip() or "member"
    try:
        get_participant_service().add_participant(
            session_id=session_id,
            agent_profile_id=agent_profile_id,
            participant_role=participant_role,
            added_by="user",
        )
        flash("Agent zum Chat hinzugefügt.", "success")
    except ProfileNotFoundError as exc:
        flash(str(exc), "error")
    return redirect(url_for("sessions.show_session", session_id=session_id))


@bp.route("/sessions/<session_id>/participants/<participant_id>/remove", methods=["POST"])
def remove_participant(session_id: str, participant_id: str):
    _get_session_or_404(session_id)
    try:
        get_participant_service().remove_participant(participant_id)
        flash("Agent aus dem Chat entfernt.", "success")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("sessions.show_session", session_id=session_id))


@bp.route("/sessions/<session_id>/message", methods=["POST"])
def post_message(session_id: str):
    session = _get_session_or_404(session_id)

    body = request.form.get("body", "").strip()
    if not body:
        flash("Message body cannot be empty.", "error")
        return redirect(url_for("sessions.show_session", session_id=session_id))

    user_id = request.form.get("user_id", "web-user")
    message_kind = request.form.get("message_kind", "conversation")

    routing = get_routing_service()
    channel = _resolve_channel_for_session(session)
    if channel is None:
        orch = get_orchestrator()
        channel = orch.create_channel(
            workspace_id=session.workspace_id,
            channel_kind="system",
            title="web-default",
        )

    payload = {
        "envelope": "user_web_post",
        "body": body,
        "from": user_id,
    }

    try:
        routing.route_message(
            workspace_id=session.workspace_id,
            channel_id=channel.channel_id,
            source_type=SOURCE_TYPE_USER,
            source_id=user_id,
            target_type=TARGET_TYPE_ORCHESTRATOR,
            target_id="@orchestrator",
            message_kind=message_kind,
            session_id=session.session_id,
            payload_ref=json.dumps(payload),
        )
        participants = get_participant_service().list_active_participant_details(session_id)
        for participant in participants:
            routing.route_orchestrator_dispatch(
                workspace_id=session.workspace_id,
                channel_id=channel.channel_id,
                orchestrator_id="@orchestrator",
                worker_id=participant["binding_id"],
                message_kind="dispatch",
                session_id=session.session_id,
                payload_ref=json.dumps(
                    {
                        "envelope": "chat_dispatch",
                        "body": body,
                        "from": user_id,
                        "participant_id": participant["participant_id"],
                    }
                ),
            )
        if participants:
            mode = current_app.config.get("WORKBENCH_AGENT_RESPONSE_MODE", "async")
            if mode == "sync":
                AgentRuntimeService(get_db()).generate_for_session(
                    session_id=session.session_id,
                    user_body=body,
                    user_id=user_id,
                )
            else:
                launch_agent_responses_async(
                    db_path=current_app.config["WORKBENCH_DB_PATH"],
                    session_id=session.session_id,
                    user_body=body,
                    user_id=user_id,
                )
    except ValueError as e:
        flash(f"Routing rejected the message: {e}", "error")
        return redirect(url_for("sessions.show_session", session_id=session_id))

    return redirect(url_for("sessions.show_session", session_id=session_id))


@bp.route("/sessions/<session_id>/status", methods=["POST"])
def update_status(session_id: str):
    session_svc: SessionService = get_session_service()
    new_status = request.form.get("status", "").strip()
    if new_status not in SESSION_STATUSES:
        abort(400, description=f"Invalid status: {new_status!r}")

    try:
        session_svc.update_session_status(session_id, status=new_status)
    except (SessionNotFoundError, ValueError) as e:
        abort(400, description=str(e))

    return redirect(url_for("sessions.show_session", session_id=session_id))


@bp.route("/sessions/<session_id>/title", methods=["POST"])
def update_title(session_id: str):
    """Update the session title."""
    session_svc: SessionService = get_session_service()
    title = (request.form.get("title") or "").strip()
    try:
        session_svc.update_session_title(session_id, title=title or None)
        flash("Session title updated.", "success")
    except SessionNotFoundError:
        abort(404, description=f"Session {session_id!r} not found")
    return redirect(url_for("sessions.session_config", session_id=session_id))


@bp.route("/sessions/<session_id>/max-tool-iterations", methods=["POST"])
def update_max_tool_iterations(session_id: str):
    """Update the max_tool_iterations for a session."""
    session_svc: SessionService = get_session_service()
    raw = (request.form.get("max_tool_iterations") or "").strip()
    try:
        val = int(raw)
    except (ValueError, TypeError):
        flash("Invalid value: must be a positive integer.", "error")
        return redirect(url_for("sessions.session_config", session_id=session_id))
    try:
        session_svc.update_session_max_tool_iterations(session_id, val)
        flash(f"Tool call limit updated to {val}.", "success")
    except (SessionNotFoundError, ValueError) as e:
        flash(str(e), "error")
    return redirect(url_for("sessions.session_config", session_id=session_id))


@bp.route("/sessions/<session_id>/delete", methods=["POST"])
def delete_session(session_id: str):
    """Delete a session and all associated data."""
    session_svc: SessionService = get_session_service()
    try:
        session_svc.delete_session(session_id)
        flash("Session deleted.", "success")
    except SessionNotFoundError:
        abort(404, description=f"Session {session_id!r} not found")
    return redirect(url_for("channels.index"))


# ── Agent status / stop ────────────────────────────────────────────────


@bp.route("/sessions/<session_id>/agent-status")
def agent_status(session_id: str):
    """Return live agent statuses for a session as JSON."""
    from agent_workbench.services.agent_status import AgentStatusTracker
    tracker = AgentStatusTracker.get_instance()
    statuses = tracker.get_session_statuses(session_id)
    return {
        "agents": [
            {
                "agent_name": s.agent_name,
                "status": s.status,
                "iteration_count": s.iteration_count,
                "current_step": {
                    "iteration": s.current_step.iteration,
                    "tool_name": s.current_step.tool_name,
                    "tool_arguments": s.current_step.tool_arguments,
                    "tool_result": s.current_step.tool_result,
                    "status": s.current_step.status,
                } if s.current_step else None,
                "error": s.error,
                "started_at": s.started_at,
            }
            for s in statuses
        ]
    }


@bp.route("/sessions/<session_id>/stop-agent", methods=["POST"])
def stop_agent(session_id: str):
    """Signal an agent to stop."""
    from agent_workbench.services.agent_status import AgentStatusTracker
    agent_name = (request.form.get("agent_name") or "").strip()
    if not agent_name:
        abort(400, description="agent_name is required")
    tracker = AgentStatusTracker.get_instance()
    stopped = tracker.stop_agent(session_id, agent_name)
    if stopped:
        flash(f"Stop signal sent to {agent_name}.", "info")
    else:
        flash(f"{agent_name} is not currently running.", "info")
    return redirect(url_for("sessions.show_session", session_id=session_id))


@bp.route("/invocations/<invocation_id>/detail")
def invocation_detail(invocation_id: str):
    """Return JSON details for a tool invocation (used by the detail panel)."""
    from agent_workbench.models.tool_invocation import ToolInvocationRepository
    conn = get_db()
    inv = ToolInvocationRepository(conn).get_by_id(invocation_id)
    if inv is None:
        abort(404, description=f"Invocation {invocation_id!r} not found")
    return {
        "invocation_id": inv.invocation_id,
        "tool_name": inv.tool_name,
        "tool_harness_type": inv.tool_harness_type,
        "status": inv.status,
        "arguments_json": inv.arguments_json,
        "result_text": inv.result_text,
        "error_text": inv.error_text,
        "harness_run_id": inv.harness_run_id,
        "created_at": inv.created_at,
        "completed_at": inv.completed_at,
    }


# -----------------------------------------------------------------------
# Tool-call confirmation
# -----------------------------------------------------------------------


_CONFIRM_DECISIONS = {"no": "denied", "yes_once": "once", "yes_permanent": "permanent"}


@bp.route("/sessions/<session_id>/tools/confirm", methods=["POST"])
def confirm_tool_call(session_id: str):
    """Resolve a pending-confirmation tool call.

    Form fields:
    - ``invocation_id`` — the ToolInvocation in ``pending_confirmation``
    - ``decision`` — one of ``no`` / ``yes_once`` / ``yes_permanent``

    If the decision allows execution, we re-dispatch the call now that
    a permission row exists, post the result as a regular chat message
    so the user can see what happened, and re-trigger the agent so it
    sees the tool result in its next loop iteration.
    """
    from agent_workbench.models.cross_harness_permission import (
        CrossHarnessPermissionRepository,
    )
    from agent_workbench.models.tool import ToolRepository
    from agent_workbench.services.tool_dispatcher import ToolDispatcher

    invocation_id = request.form.get("invocation_id", "").strip()
    decision = request.form.get("decision", "").strip()
    if decision not in _CONFIRM_DECISIONS:
        abort(400, description=f"Invalid decision: {decision!r}")

    conn = get_db()
    invocations = None
    from agent_workbench.models.tool_invocation import ToolInvocationRepository
    invocations = ToolInvocationRepository(conn)
    inv = invocations.get_by_id(invocation_id)
    if inv is None or inv.session_id != session_id:
        abort(404, description="Tool invocation not found in this session")
    if inv.status != "pending_confirmation":
        # Idempotent: if it's already been resolved, just redirect.
        flash(
            f"Tool-Invocation {invocation_id[:8]}… ist bereits "
            f"'{inv.status}'.",
            "info",
        )
        return redirect(
            url_for("sessions.show_session", session_id=session_id)
        )

    routing = get_routing_service()
    channel = _resolve_channel_for_session(
        SessionExtensionRepository(conn).get_by_id(session_id)  # type: ignore[arg-type]
    )

    if decision == "no":
        # Mark original as denied, post a system message so the chat shows it.
        invocations.update_status(
            inv.invocation_id,
            status="denied",
            error_text="User denied this cross-harness call.",
        )
        if channel is not None:
            payload = json.dumps({
                "envelope": "tool_confirmation_decision",
                "invocation_id": inv.invocation_id,
                "tool_name": inv.tool_name,
                "decision": "denied",
                "by": "user",
            })
            routing.route_message(
                workspace_id=inv.workspace_id,
                channel_id=channel.channel_id,
                source_type="system",
                source_id="user",
                target_type="all",
                target_id="@all",
                message_kind="system",
                session_id=session_id,
                payload_ref=payload,
            )
        flash(f"Tool-Call abgelehnt.", "info")
        return redirect(
            url_for("sessions.show_session", session_id=session_id)
        )

    # yes_once / yes_permanent — record the permission, then re-dispatch.
    cross_perms = CrossHarnessPermissionRepository(conn)

    # The reason stores the agent harness — pull it back out so the
    # permission row is precise.
    from agent_workbench.services.tool_dispatcher import (
        extract_agent_harness_from_reason,
        reconstruct_tool_call,
    )
    agent_harness = extract_agent_harness_from_reason(
        inv.confirmation_reason or ""
    )

    cross_perms.grant(
        session_id=session_id,
        workspace_id=inv.workspace_id,
        agent_harness_type=agent_harness,
        tool_harness_type=inv.tool_harness_type,
        decision=_CONFIRM_DECISIONS[decision],  # type: ignore[arg-type]
    )

    # Re-dispatch the original call.
    tool_repo = ToolRepository(conn)
    tool = tool_repo.get_by_id(inv.tool_id)
    if tool is None:
        abort(404, description="Tool no longer exists")

    dispatcher = ToolDispatcher(conn)
    tool_call = reconstruct_tool_call(inv)
    # The session policy isn't known here, so we pass a permissive
    # default; the tool was already approved for the original call
    # and the policy hasn't changed in the meantime.
    session_policy_raw = request.form.get("session_policy", "")
    if session_policy_raw:
        session_policy = [p.strip() for p in session_policy_raw.split(",") if p.strip()]
    else:
        # Conservative default: allow anything that was on the
        # permission-class side of the conversation.
        session_policy = ["read_only", "write_local", "write_remote", "destructive"]
    result = dispatcher.dispatch(
        session_id=session_id,
        workspace_id=inv.workspace_id,
        session_policy=session_policy,
        tool_call=tool_call,
        agent_harness_type=agent_harness,
    )

    # For "once", consume the permission row so a second call asks again.
    if decision == "yes_once" and agent_harness is not None:
        cross_perms.consume_once(
            session_id=session_id,
            agent_harness_type=agent_harness,
            tool_harness_type=inv.tool_harness_type,
        )

    # Mark the original pending invocation as completed so the UI
    # hides the confirmation form on next render.  The actual
    # outcome is recorded on the NEW invocation (in result_text /
    # error_text / harness_run_id).
    invocations.update_status(
        inv.invocation_id,
        status="completed",
        result_text=result.content,
    )

    # Post a result message so the chat shows what happened.
    if channel is not None:
        payload = json.dumps({
            "envelope": "tool_result",
            "invocation_id": inv.invocation_id,
            "tool_name": inv.tool_name,
            "status": result.status,
            "content": result.content,
            "by": "user-confirmed",
        })
        routing.route_message(
            workspace_id=inv.workspace_id,
            channel_id=channel.channel_id,
            source_type="system",
            source_id="user",
            target_type="all",
            target_id="@all",
            message_kind="tool_result",
            session_id=session_id,
            payload_ref=payload,
        )
    flash(
        f"Tool-Call {inv.tool_name!r} ausgeführt ({result.status}).",
        "success" if result.status == "completed" else "error",
    )
    return redirect(
        url_for("sessions.show_session", session_id=session_id)
    )
