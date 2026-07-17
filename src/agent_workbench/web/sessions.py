"""Sessions blueprint — session detail, chat participation, and message post."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

from agent_workbench.models.agent_profile_binding import AgentProfileBinding, AgentProfileBindingRepository
from agent_workbench.models.channel import Channel
from agent_workbench.models.project_asset import ProjectAssetRepository
from agent_workbench.models.session_extension import (
    SESSION_STATUSES,
    SessionExtension,
    SessionExtensionRepository,
)
from agent_workbench.models.session_label import SessionLabelRepository
from agent_workbench.models.task_spec import TaskSpecRepository
from agent_workbench.services.agent_runtime_service import (
    AgentRuntimeService,
    launch_agent_responses_async,
)
from agent_workbench.services.agent_status import AgentStep
from agent_workbench.services.profile_service import ProfileNotFoundError, ProfileService
from agent_workbench.services.participant_transfer_service import ParticipantTransferService
from agent_workbench.services.routing_service import (
    SOURCE_TYPE_USER,
    TARGET_TYPE_ORCHESTRATOR,
)
from agent_workbench.services.run_service import RunService
from agent_workbench.services.session_service import (
    SessionNotFoundError,
    SessionService,
)
from agent_workbench.services.team_service import TeamService
from agent_workbench.web.app import (
    get_db,
    get_orchestrator,
    get_participant_service,
    get_routing_service,
    get_session_service,
)
from agent_workbench.web.messages import _load_participant_index, _load_user_index, visible_messages_for_session

bp = Blueprint("sessions", __name__)


def _get_session_or_404(session_id: str) -> SessionExtension:
    repo = SessionExtensionRepository(get_db())
    sess = repo.get_by_id(session_id)
    if sess is None:
        abort(404, description=f"Session {session_id!r} not found")
    return sess


def _resolve_channel_for_session(session) -> Optional[Channel]:
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


def _resolve_binding(session) -> Optional[AgentProfileBinding]:
    repo = AgentProfileBindingRepository(get_db())
    return repo.get_latest_for_session(session.session_id)


def _latest_profiles_for_picker() -> list:
    return ProfileService(get_db()).list_latest_profiles()


def _parse_agent_mention(body: str) -> Optional[str]:
    """Extract an @agent_name from the message body, if present.

    Looks for ``@word`` at the start of the body or after whitespace.
    Returns the agent name (without ``@``) or ``None``.
    """
    m = re.search(r"(?:^|\s)@(\w[\w-]*)", body)
    if m:
        return m.group(1)
    return None


def _parse_agent_mentions(body: str) -> List[str]:
    """Return unique @agent names in message order."""
    names = re.findall(r"(?:^|\s)@(\w[\w-]*)", body)
    seen = set()
    result = []
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            result.append(name)
    return result


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

    # Load only the latest 50 visible messages using the paginated query
    from agent_workbench.models.routed_message import RoutedMessageRepository
    repo = RoutedMessageRepository(get_db())
    messages, oldest_cursor, has_earlier = repo.list_visible_before(
        session_id, limit=50,
    )

    # Run-Liste + Start-Picker (aditive UI-Schicht).
    run_service = RunService(get_db())
    session_runs = run_service.list_for_session(session_id)
    available_harness_types = run_service.available_harness_types()

    # TaskSpec-Picker
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

    # Session labels for the session type
    label_repo = SessionLabelRepository(get_db())
    labels = label_repo.list_by_workspace(session.workspace_id)
    label_display = {}
    label_colors = {}
    label_descriptions = {}
    for lbl in labels:
        label_display[lbl.name] = lbl.display_name or lbl.name
        label_colors[lbl.name] = lbl.color
        label_descriptions[lbl.name] = lbl.description

    # Project assets for this workspace
    asset_repo = ProjectAssetRepository(get_db())
    project_assets = asset_repo.list_by_workspace(session.workspace_id)

    current_user_id = g.current_user.user_id
    current_user_display = g.current_user.display_name
    from agent_workbench.models.workspace import WorkspaceRepository
    workspace = WorkspaceRepository(get_db()).get_by_id(session.workspace_id)
    teams = TeamService(get_db()).list_teams(session.workspace_id)

    return render_template(
        "session_view.html",
        session=session,
        session_id=session_id,
        messages=messages,
        has_earlier_messages=has_earlier,
        oldest_message_cursor=oldest_cursor,
        users=_load_user_index(),
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
        session_label_display=label_display,
        session_label_colors=label_colors,
        session_label_descriptions=label_descriptions,
        project_assets=project_assets,
        current_user_id=current_user_id,
        current_user_display=current_user_display,
        workspace=workspace,
        teams=teams,
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


@bp.route("/sessions/<session_id>/teams/<team_id>/apply", methods=["POST"])
def apply_team(session_id: str, team_id: str):
    session = _get_session_or_404(session_id)
    team_service = TeamService(get_db())
    team = team_service.get_team(team_id)
    if team is None or team.workspace_id != session.workspace_id:
        abort(404)
    added = 0
    participant_service = get_participant_service()
    for member in team_service.list_members(team_id):
        before = len(participant_service.list_active_participants(session_id))
        participant_service.add_participant(
            session_id=session_id,
            agent_profile_id=member.agent_profile_id,
            participant_role="member",
            added_by="system",
        )
        after = len(participant_service.list_active_participants(session_id))
        added += int(after > before)
    flash(f"Applied team {team.name!r}: {added} agent(s) added.", "success")
    return redirect(url_for("sessions.show_session", session_id=session_id))


@bp.route("/sessions/<session_id>/message", methods=["POST"])
def post_message(session_id: str):
    session = _get_session_or_404(session_id)

    body = request.form.get("body", "").strip()
    if not body:
        flash("Message body cannot be empty.", "error")
        return redirect(url_for("sessions.show_session", session_id=session_id))

    user_id = g.current_user.user_id
    message_kind = request.form.get("message_kind", "conversation")

    routing = get_routing_service()
    channel = _resolve_channel_for_session(session)
    if channel is None:
        orch = get_orchestrator()
        channel = orch.create_channel(
            workspace_id=session.workspace_id,
            channel_kind=session.session_type,
            title="web-default",
            default_target=None,
        )
        orch.channels.update_active_session(
            channel.channel_id,
            active_session_id=session.session_id,
        )
    payload = {
        "envelope": "user_web_post",
        "body": body,
        "from": user_id,
    }

    explicit_targets = [
        name.strip() for name in request.form.getlist("target_agents") if name.strip()
    ]
    mentioned_targets = _parse_agent_mentions(body) if not explicit_targets else []
    target_agent_names = explicit_targets or mentioned_targets
    target_agent_name = target_agent_names[0] if len(target_agent_names) == 1 else None
    # Addressing tokens are routing syntax, so strip mention-derived tokens
    # from the LLM input while preserving the original visible message.
    clean_body = body
    for mentioned_name in mentioned_targets:
        pattern = re.compile(r"\B@" + re.escape(mentioned_name) + r"\b", re.IGNORECASE)
        clean_body = pattern.sub("", clean_body)
    clean_body = clean_body.strip()

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

        # Filter to the explicitly selected/mentioned subset, or all.
        if target_agent_names:
            target_keys = {name.lower() for name in target_agent_names}
            filtered = [
                p for p in participants
                if p["agent_name"].lower() in target_keys
            ]
            found_keys = {p["agent_name"].lower() for p in filtered}
            missing = [
                name for name in target_agent_names if name.lower() not in found_keys
            ]
            if missing:
                flash(f"Agent target(s) not found: {', '.join(missing)}.", "error")
                return redirect(url_for("sessions.show_session", session_id=session_id))
            dispatch_targets = filtered
        else:
            dispatch_targets = participants

        for participant in dispatch_targets:
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
        if dispatch_targets:
            mode = current_app.config.get("WORKBENCH_AGENT_RESPONSE_MODE", "async")
            if mode == "sync":
                AgentRuntimeService(get_db()).generate_for_session(
                    session_id=session.session_id,
                    user_body=clean_body,
                    user_id=user_id,
                    target_agent_name=target_agent_name,
                    target_agent_names=target_agent_names or None,
                )
            else:
                launch_agent_responses_async(
                    db_path=current_app.config["WORKBENCH_DB_PATH"],
                    session_id=session.session_id,
                    user_body=clean_body,
                    user_id=user_id,
                    target_agent_name=target_agent_name,
                    target_agent_names=target_agent_names or None,
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
        if new_status == "archived":
            from agent_workbench.services.agent_status import AgentStatusTracker
            AgentStatusTracker.get_instance().cleanup_session(session_id)
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


@bp.route("/sessions/<session_id>/max-auto-turns", methods=["POST"])
def update_max_auto_turns(session_id: str):
    """Update the max_auto_turns for a session."""
    session_svc: SessionService = get_session_service()
    raw = (request.form.get("max_auto_turns") or "").strip()
    try:
        val = int(raw)
    except (ValueError, TypeError):
        flash("Invalid value: must be a non-negative integer.", "error")
        return redirect(url_for("sessions.session_config", session_id=session_id))
    try:
        session_svc.update_session_max_auto_turns(session_id, val)
        flash(f"Auto-turn limit updated to {val}.", "success")
    except (SessionNotFoundError, ValueError) as e:
        flash(str(e), "error")
    return redirect(url_for("sessions.session_config", session_id=session_id))


@bp.route("/sessions/<session_id>/transfer", methods=["POST"])
def transfer_session(session_id: str):
    """Fork a session and copy the selected active participants."""
    source = _get_session_or_404(session_id)
    participant_ids = request.form.getlist("participant_ids") or None
    try:
        target, transfer = ParticipantTransferService(get_db()).transfer_to_new_session(
            source_session_id=session_id,
            session_type=(request.form.get("session_type") or source.session_type).strip(),
            title=(request.form.get("title") or "").strip() or None,
            participant_ids=participant_ids,
            context_summary=(request.form.get("context_summary") or "").strip(),
            initiated_by="user",
        )
    except (LookupError, ValueError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("sessions.session_config", session_id=session_id))
    flash(f"Continuation session created ({transfer.transfer_id[:8]}…).", "success")
    return redirect(url_for("sessions.show_session", session_id=target.session_id))


@bp.route("/sessions/<session_id>/delete", methods=["POST"])
def delete_session(session_id: str):
    """Delete a session and all associated data."""
    session_svc: SessionService = get_session_service()
    # Capture workspace before deletion so we can redirect back to it.
    from agent_workbench.models.session_extension import SessionExtensionRepository
    repo = SessionExtensionRepository(get_db())
    sess = repo.get_by_id(session_id)
    workspace_id = sess.workspace_id if sess else None
    try:
        session_svc.delete_session(session_id)
        flash("Session deleted.", "success")
    except SessionNotFoundError:
        abort(404, description=f"Session {session_id!r} not found")
    if workspace_id:
        return redirect(url_for("channels.index", workspace_id=workspace_id))
    return redirect(url_for("channels.index"))


# ── Session export ────────────────────────────────────────────────────


@bp.route("/sessions/<session_id>/export", methods=["GET"])
def export_session(session_id: str):
    """Export session messages in markdown or JSON format.

    Query parameters:
        format (str): ``"markdown"`` or ``"json"`` (required).

    Returns a ``Content-Disposition: attachment`` response with a
    sanitized filename. Only chat-visible messages (non-dispatch) are
    included, in chronological order.
    """
    session = _get_session_or_404(session_id)
    export_format = (request.args.get("format") or "").strip().lower()

    if export_format not in ("markdown", "json"):
        abort(400, description="Invalid format. Use 'markdown' or 'json'.")

    messages = visible_messages_for_session(session_id)

    # Build an ASCII-only filename suitable for the WSGI header boundary.
    base = (session.title or session.session_id[:12]).strip()
    safe_base = secure_filename(base) or session.session_id[:12]
    ext = ".md" if export_format == "markdown" else ".json"
    filename = f"{safe_base}{ext}"

    if export_format == "markdown":
        body = _render_export_markdown(session, messages)
        return Response(
            body,
            mimetype="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        body = _render_export_json(session, messages)
        return Response(
            body,
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


def _render_export_markdown(
    session: SessionExtension,
    messages: list,
) -> str:
    """Render session messages as a Markdown document."""
    lines: list[str] = []
    lines.append(f"# Session: {session.title or session.session_id}")
    lines.append("")
    lines.append(f"- **ID**: {session.session_id}")
    lines.append(f"- **Type**: {session.session_type}")
    lines.append(f"- **Status**: {session.status}")
    lines.append("")

    # Resolve participant names
    participants = _load_participant_index(session.session_id)
    users = _load_user_index()

    for msg in messages:
        source_type = getattr(msg, "source_type", "") or ""
        source_id = getattr(msg, "source_id", "") or ""
        created_at = getattr(msg, "created_at", 0)
        payload_ref = getattr(msg, "payload_ref", None)

        # Resolve display name
        if source_type == "user":
            display = users.get(source_id, source_id or "user")
        elif source_type in ("agent", "orchestrator", "worker"):
            display = participants.get(source_id, source_id[:8])
        elif source_type == "system":
            display = "System"
        else:
            display = source_id or "unknown"

        ts_str = ""
        if created_at:
            from datetime import UTC, datetime
            try:
                ts_str = datetime.fromtimestamp(float(created_at), UTC).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                )
            except (TypeError, ValueError, OSError):
                ts_str = str(created_at)

        # Extract body text from payload
        body_text = ""
        if payload_ref:
            try:
                payload = json.loads(payload_ref)
                if isinstance(payload, dict):
                    body_value = (
                        payload["body"] if "body" in payload else payload.get("text", "")
                    )
                    if isinstance(body_value, str):
                        body_text = body_value
                    elif body_value is not None:
                        body_text = json.dumps(body_value, ensure_ascii=False, default=str)
            except (json.JSONDecodeError, TypeError):
                body_text = payload_ref

        lines.append(f"### {display} — {ts_str}")
        if body_text:
            lines.append("")
            lines.append(body_text)
        lines.append("")

    return "\n".join(lines)


def _render_export_json(
    session: SessionExtension,
    messages: list,
) -> str:
    """Render session messages as a JSON document."""
    participants = _load_participant_index(session.session_id)
    users = _load_user_index()

    export_messages: list[dict] = []
    for msg in messages:
        source_type = getattr(msg, "source_type", "") or ""
        source_id = getattr(msg, "source_id", "") or ""
        payload_ref = getattr(msg, "payload_ref", None)

        # Resolve display name
        if source_type == "user":
            display = users.get(source_id, source_id or "user")
        elif source_type in ("agent", "orchestrator", "worker"):
            display = participants.get(source_id, source_id[:8])
        elif source_type == "system":
            display = "System"
        else:
            display = source_id or "unknown"

        # Parse payload where possible
        parsed_payload: dict | str | None = None
        if payload_ref:
            try:
                parsed_payload = json.loads(payload_ref)
            except (json.JSONDecodeError, TypeError):
                parsed_payload = payload_ref

        entry = {
            "routed_message_id": getattr(msg, "routed_message_id", ""),
            "source_type": source_type,
            "source_id": source_id,
            "target_type": getattr(msg, "target_type", ""),
            "target_id": getattr(msg, "target_id", ""),
            "message_kind": getattr(msg, "message_kind", ""),
            "created_at": getattr(msg, "created_at", 0),
            "display_name": display,
            "payload": parsed_payload,
        }
        export_messages.append(entry)

    doc = {
        "session": {
            "session_id": session.session_id,
            "workspace_id": session.workspace_id,
            "session_type": session.session_type,
            "status": session.status,
            "title": session.title,
            "created_at": session.created_at,
        },
        "messages": export_messages,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False, default=str)


# ── Managed upload ────────────────────────────────────────────────────


@bp.route("/sessions/<session_id>/assets/upload", methods=["POST"])
def upload_session_file(session_id: str):
    """Upload a file scoped to a session and workspace.

    Multipart form field ``file`` is required. The file is stored under
    ``WORKBENCH_UPLOAD_ROOT`` (default ``<project_root>/var/uploads``)
    with a UUID storage name. A ``project_assets`` row of type ``file``
    is created with the absolute managed path, the original basename as
    label, and the session_id set.

    Returns JSON with asset fields for AJAX callers.
    """
    session = _get_session_or_404(session_id)

    # Validate the file field
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    original_filename = (file.filename or "").strip()

    if not original_filename:
        return jsonify({"error": "Empty filename"}), 400

    # Reject directory uploads (trailing / or \)
    if original_filename.endswith("/") or original_filename.endswith("\\"):
        return jsonify({"error": "Directory uploads are not allowed"}), 400

    # Reject control characters. Path components are discarded below; the
    # human-facing basename itself is safe to preserve because storage never
    # uses it and every UI renderer escapes/text-assigns labels.
    if any(ord(char) < 32 for char in original_filename):
        return jsonify({"error": "Invalid filename"}), 400

    display_basename = original_filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if display_basename in {"", ".", ".."}:
        return jsonify({"error": "Invalid filename"}), 400

    # Resolve upload root
    upload_root = current_app.config.get(
        "WORKBENCH_UPLOAD_ROOT",
        str(Path(__file__).resolve().parents[3] / "var" / "uploads"),
    )
    upload_root_path = Path(upload_root).expanduser().resolve()
    workspace_upload_path = (upload_root_path / session.workspace_id).resolve()
    try:
        workspace_upload_path.relative_to(upload_root_path)
    except ValueError:
        return jsonify({"error": "Invalid workspace upload path"}), 400
    workspace_upload_path.mkdir(parents=True, exist_ok=True)

    # Max upload size
    max_bytes = current_app.config.get("WORKBENCH_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)

    # Generate UUID storage name
    import uuid
    storage_name = uuid.uuid4().hex
    temp_path = workspace_upload_path / f"{storage_name}.tmp"
    final_path = workspace_upload_path / storage_name

    # Stream to temp file with hard size limit
    bytes_written = 0
    try:
        with open(temp_path, "wb") as f:
            while True:
                chunk = file.read(65536)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    f.close()
                    temp_path.unlink(missing_ok=True)
                    return jsonify({"error": f"File too large (max {max_bytes} bytes)"}), 413
                f.write(chunk)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    # Atomic rename.  A filesystem error must not strand the temporary file.
    try:
        temp_path.rename(final_path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise

    # Resolve containment: final_path must be under upload_root_path
    try:
        final_path.resolve().relative_to(upload_root_path)
    except ValueError:
        final_path.unlink(missing_ok=True)
        return jsonify({"error": "Path containment violation"}), 400

    managed_path = str(final_path.resolve())

    # Create project_assets row
    asset_repo = ProjectAssetRepository(get_db())
    try:
        asset = asset_repo.create(
            workspace_id=session.workspace_id,
            asset_type="file",
            path=managed_path,
            label=display_basename,
            session_id=session.session_id,
        )
    except Exception:
        # DB failure: clean up the file
        final_path.unlink(missing_ok=True)
        raise

    return jsonify({
        "asset_id": asset.asset_id,
        "path": asset.path,
        "label": asset.label,
        "asset_type": asset.asset_type,
        "session_id": asset.session_id,
        "workspace_id": asset.workspace_id,
    })


# ── Agent status / stop ────────────────────────────────────────────────


# Truncation limit for tool_result in the agent-status poll endpoint.
# 8,192 characters is generous enough for a useful preview while keeping 1s polling
# payloads small.  The full result is always available via the invocation
# detail endpoint.
_AGENT_STATUS_RESULT_TRUNCATION = 8192


def _truncate_result(result: Optional[str], limit: int = _AGENT_STATUS_RESULT_TRUNCATION) -> Optional[str]:
    """Truncate *result* to *limit* characters, appending a marker."""
    if result is None:
        return None
    if len(result) <= limit:
        return result
    return result[:limit] + "\n… [truncated]"


def _serialize_step(step: "AgentStep") -> Dict[str, Any]:
    """Serialize one AgentStep to a JSON-safe dict with truncated result."""
    return {
        "iteration": step.iteration,
        "tool_name": step.tool_name,
        "tool_arguments": step.tool_arguments,
        "tool_result": _truncate_result(step.tool_result),
        "status": step.status,
        "started_at": step.started_at,
        "completed_at": step.completed_at,
    }


@bp.route("/sessions/<session_id>/agent-status")
def agent_status(session_id: str):
    """Return live agent statuses for a session as JSON.

    Returns every accumulated step per agent (not just current_step),
    with tool_result truncated to 8,192 characters for bounded 1s polling.
    ``current_step`` is kept for backward compatibility.
    """
    from agent_workbench.services.agent_status import AgentStatusTracker
    tracker = AgentStatusTracker.get_instance()
    statuses = tracker.get_session_statuses(session_id)
    return {
        "agents": [
            {
                "agent_name": s.agent_name,
                "status": s.status,
                "iteration_count": s.iteration_count,
                "current_step": _serialize_step(s.current_step) if s.current_step else None,
                "steps": [_serialize_step(step) for step in s.steps],
                "error": s.error,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
            }
            for s in statuses
        ]
    }


@bp.route("/sessions/<session_id>/stop-all-agents", methods=["POST"])
def stop_all_agents(session_id: str):
    """Signal every tracked agent in a session without navigating the page."""
    _get_session_or_404(session_id)
    from agent_workbench.services.agent_status import AgentStatusTracker

    tracker = AgentStatusTracker.get_instance()
    requested = request.get_json(silent=True) or {}
    names = requested.get("agent_names") if isinstance(requested, dict) else None
    if not names:
        names = [s.agent_name for s in tracker.get_session_statuses(session_id)]
    stopped = []
    inactive = []
    for name in dict.fromkeys(str(n).strip() for n in names if str(n).strip()):
        if tracker.stop_agent(session_id, name):
            stopped.append(name)
        else:
            inactive.append(name)
    return {"stopped": stopped, "inactive": inactive}


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
    from agent_workbench.models.tool import PERMISSION_CLASSES, ToolRepository
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
        flash("Tool-Call abgelehnt.", "info")
        return redirect(
            url_for("sessions.show_session", session_id=session_id)
        )

    # yes_once / yes_permanent — record the permission, then re-dispatch.
    cross_perms = CrossHarnessPermissionRepository(conn)

    from agent_workbench.services.tool_dispatcher import (
        reconstruct_tool_call,
    )

    # Use the stored confirmation context for redispatch, ignoring any
    # posted session_policy.  If context is missing/malformed, fail
    # closed without running the adapter.
    ctx = inv.confirmation_context_json
    required_context_keys = {
        "agent_harness_type",
        "session_policy",
        "allowed_tool_names",
    }
    context_shape_valid = (
        isinstance(ctx, dict)
        and required_context_keys.issubset(ctx)
        and isinstance(ctx.get("agent_harness_type"), str)
        and bool(ctx.get("agent_harness_type", "").strip())
        and (
            ctx.get("session_policy") is None
            or (
                isinstance(ctx.get("session_policy"), list)
                and all(
                    isinstance(item, str) and item in PERMISSION_CLASSES
                    for item in ctx["session_policy"]
                )
            )
        )
        and (
            ctx.get("allowed_tool_names") is None
            or (
                isinstance(ctx.get("allowed_tool_names"), list)
                and all(
                    isinstance(item, str) and bool(item)
                    for item in ctx["allowed_tool_names"]
                )
            )
        )
    )
    if not context_shape_valid:
        invocations.update_status(
            inv.invocation_id,
            status="denied",
            error_text="Confirmation context is missing or malformed; failing closed.",
        )
        flash("Confirmation context missing; tool call denied (fail closed).", "error")
        return redirect(
            url_for("sessions.show_session", session_id=session_id)
        )

    assert isinstance(ctx, dict)  # narrowed by the fail-closed guard above
    agent_harness = ctx["agent_harness_type"]
    stored_session_policy: Optional[List[str]] = ctx["session_policy"]
    stored_allowed_tool_names: Optional[List[str]] = ctx["allowed_tool_names"]

    cross_perms.grant(
        session_id=session_id,
        workspace_id=inv.workspace_id,
        agent_harness_type=agent_harness,
        tool_harness_type=inv.tool_harness_type,
        decision=_CONFIRM_DECISIONS[decision],  # type: ignore[arg-type]
    )

    # Re-dispatch the original call using only stored context.
    tool_repo = ToolRepository(conn)
    tool = tool_repo.get_by_id(inv.tool_id)
    if tool is None:
        abort(404, description="Tool no longer exists")

    dispatcher = ToolDispatcher(conn)
    tool_call = reconstruct_tool_call(inv)
    result = dispatcher.dispatch(
        session_id=session_id,
        workspace_id=inv.workspace_id,
        session_policy=stored_session_policy,
        tool_call=tool_call,
        agent_harness_type=agent_harness,
        allowed_tool_names=stored_allowed_tool_names,
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
