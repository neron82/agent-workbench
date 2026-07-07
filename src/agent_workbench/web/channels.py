"""Channels blueprint — list, create, and fork channel routes.

Routes
------
* ``GET  /``                              redirect to ``/channels``
* ``GET  /channels``                      list channels in a workspace
* ``POST /channels``                      create a new channel
* ``GET  /channels/<channel_id>``         show a single channel + its active session
* ``POST /channels/<channel_id>/fork``    create a fork (session type transition)

The current workspace is resolved with a simple convention: a ``workspace_id``
query parameter if provided, otherwise the *default workspace* from the DB
(``is_default=1``) is used. If no default exists and no parameter is given,
the routes render the channel list with an empty workspace, which makes
the "create channel" affordance show a workspace selector.
"""

from __future__ import annotations

from typing import Optional

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from agent_workbench.models.channel import CHANNEL_KINDS, ChannelRepository
from agent_workbench.models.session_extension import (
    SESSION_TYPES,
    SessionExtensionRepository,
)
from agent_workbench.models.workspace import WorkspaceRepository
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


bp = Blueprint("channels", __name__)


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------


def _resolve_workspace_id() -> Optional[str]:
    """Return the workspace id from the request or the default workspace.

    On a brand-new database we bootstrap the MVP's single-user default
    workspace so the first page load is immediately usable instead of
    dumping the operator onto a politely empty void.
    """
    ws_id = request.values.get("workspace_id")
    if ws_id:
        return ws_id
    repo = WorkspaceRepository(get_db())
    default = repo.get_default(tenant_id="default")
    if default is not None:
        return default.workspace_id
    # Fall back to the most recent workspace if no explicit default exists.
    all_ws = repo.list_all()
    if all_ws:
        return all_ws[0].workspace_id

    # First-run bootstrap: create the default single-user workspace that
    # the MVP assumes exists, so the web UI renders usable channel lists
    # and creation forms on first launch.
    created = repo.create(
        tenant_id="default",
        name="Default Workspace",
        is_default=True,
    )
    return created.workspace_id


def _get_channel_or_404(channel_id: str):
    repo = ChannelRepository(get_db())
    ch = repo.get_by_id(channel_id)
    if ch is None:
        abort(404, description=f"Channel {channel_id!r} not found")
    return ch


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("/")
def index():
    """Landing page — quick session start + recent sessions."""
    workspace_id = _resolve_workspace_id()
    if not workspace_id:
        return render_template("landing.html", workspace_id=None, recent_sessions=[])

    # Recent sessions across all types, newest first
    from agent_workbench.models.session_extension import SessionExtensionRepository
    repo = SessionExtensionRepository(get_db())
    all_sessions = repo.list_by_workspace(workspace_id)
    all_sessions.sort(key=lambda s: s.created_at, reverse=True)
    recent = all_sessions[:20]

    return render_template(
        "landing.html",
        workspace_id=workspace_id,
        recent_sessions=recent,
    )


@bp.route("/sessions/type/<session_type>")
def list_by_type(session_type: str):
    """List sessions of a given type (chat/research/work)."""
    if session_type not in ("chat", "research", "work"):
        abort(400, description=f"Invalid session type: {session_type!r}")

    workspace_id = _resolve_workspace_id()
    if not workspace_id:
        return render_template("session_list.html", session_type=session_type, sessions=[], workspace_id=None)

    from agent_workbench.models.session_extension import SessionExtensionRepository
    repo = SessionExtensionRepository(get_db())
    all_sessions = repo.list_by_workspace(workspace_id)
    filtered = [s for s in all_sessions if s.session_type == session_type]
    filtered.sort(key=lambda s: s.created_at, reverse=True)

    # Resolve channel for each session
    channels_map = {}
    for s in filtered:
        row = get_db().execute(
            "SELECT channel_id, title FROM channels WHERE active_session_id = ? LIMIT 1",
            (s.session_id,),
        ).fetchone()
        if row:
            channels_map[s.session_id] = dict(row)

    return render_template(
        "session_list.html",
        session_type=session_type,
        sessions=filtered,
        channels_map=channels_map,
        workspace_id=workspace_id,
    )


@bp.route("/channels", methods=["GET"])
def list_channels():
    """Render the channel list grouped by channel_kind."""
    workspace_id = _resolve_workspace_id()
    workspaces = WorkspaceRepository(get_db()).list_all()

    channels_by_kind: dict[str, list] = {
        "chat": [],
        "research": [],
        "work": [],
    }
    if workspace_id is not None:
        chans = ChannelRepository(get_db()).list_by_workspace(workspace_id)
        for ch in chans:
            if ch.channel_kind in channels_by_kind:
                channels_by_kind[ch.channel_kind].append(ch)

    # Pre-resolve the active session for each channel so the template
    # can render the link without hitting the DB.
    session_repo = SessionExtensionRepository(get_db())
    active_sessions: dict[str, object] = {}
    for kind, items in channels_by_kind.items():
        for ch in items:
            if ch.active_session_id:
                active_sessions[ch.channel_id] = session_repo.get_by_id(
                    ch.active_session_id
                )

    return render_template(
        "channel_list.html",
        workspace_id=workspace_id,
        workspaces=workspaces,
        channel_kinds=CHANNEL_KINDS,
        channels_by_kind=channels_by_kind,
        active_sessions=active_sessions,
    )


@bp.route("/channels", methods=["POST"])
def create_channel():
    """Create a new channel and (optionally) a starter session."""
    workspace_id = request.form.get("workspace_id") or _resolve_workspace_id()
    if not workspace_id:
        abort(
            400,
            description=(
                "workspace_id is required to create a channel "
                "(no default workspace found)."
            ),
        )

    channel_kind = request.form.get("channel_kind", "chat")
    title = request.form.get("title", "").strip()
    create_session = request.form.get("create_session") in ("1", "true", "on")

    if channel_kind not in CHANNEL_KINDS:
        abort(400, description=f"Invalid channel_kind: {channel_kind!r}")

    orch = get_orchestrator()
    channel = orch.create_channel(
        workspace_id=workspace_id,
        channel_kind=channel_kind,
        title=title,
    )

    if create_session:
        # Spawn a session of the matching type for the new channel.
        # The session type is determined by the channel kind: chat/research/work
        # map directly; system/review channels get a chat session as a default.
        session_type_map = {
            "chat": "chat",
            "research": "research",
            "work": "work",
            "system": "chat",
            "review": "chat",
        }
        stype = session_type_map.get(channel_kind, "chat")
        if stype in SESSION_TYPES:
            session_svc = get_session_service()
            try:
                session_svc.create_session(
                    workspace_id=workspace_id,
                    session_type=stype,
                    channel_id=channel.channel_id,
                    title=title or None,
                )
            except (SessionNotFoundError, ValueError) as e:
                flash(f"Channel created, but session failed: {e}", "warning")

    return redirect(
        url_for("channels.show_channel", channel_id=channel.channel_id)
    )


@bp.route("/channels/<channel_id>", methods=["GET"])
def show_channel(channel_id: str):
    """Show a single channel with its active session and recent messages."""
    channel = _get_channel_or_404(channel_id)

    active_session = None
    messages: list = []
    participants: list = []
    active_session_runs: list = []
    if channel.active_session_id:
        sess_repo = SessionExtensionRepository(get_db())
        active_session = sess_repo.get_by_id(channel.active_session_id)
        if active_session is not None:
            messages = visible_messages_for_session(active_session.session_id)
            participants = get_participant_service().list_active_participant_details(
                active_session.session_id
            )
            # Kompakte Run-Liste (max 5) für die Channel-View.
            from agent_workbench.services.run_service import RunService
            active_session_runs = RunService(get_db()).list_for_session(
                active_session.session_id
            )

    return render_template(
        "channel_view.html",
        channel=channel,
        active_session=active_session,
        messages=messages,
        participants=participants,
        active_session_runs=active_session_runs,
    )


@bp.route("/channels/<channel_id>/fork", methods=["POST", "GET"])
def fork_channel(channel_id: str):
    """Create a structured fork for the channel's active session.

    The new session type comes from ``new_session_type`` in the form body
    (default ``research``). The fork always lives in the same channel and
    the same workspace — type changes are recorded in ``fork_records``
    and the new child session is linked to the channel as the new
    active session.

    On ``GET`` we render a small fork form so a non-JS caller can pick a
    destination type before submitting.
    """
    channel = _get_channel_or_404(channel_id)

    if request.method == "GET":
        return render_template(
            "channel_fork_form.html",
            channel=channel,
            session_types=SESSION_TYPES,
        )

    new_type = request.form.get("new_session_type", "research")
    if new_type not in SESSION_TYPES:
        abort(400, description=f"Invalid new_session_type: {new_type!r}")

    fork_reason = request.form.get("fork_reason", "User-initiated fork from web UI")
    initiated_by = request.form.get("initiated_by", "user")

    if not channel.active_session_id:
        # No active session on the channel — create a brand new session
        # of the requested type and link it.
        session_svc = get_session_service()
        try:
            new_sess = session_svc.create_session(
                workspace_id=channel.workspace_id,
                session_type=new_type,
                channel_id=channel.channel_id,
            )
        except (SessionNotFoundError, ValueError) as e:
            abort(400, description=str(e))
        return redirect(
            url_for("sessions.show_session", session_id=new_sess.session_id)
        )

    # Transition the active session's type via the structured fork flow.
    session_svc = get_session_service()
    try:
        child, _fork_record = session_svc.transition_session_type(
            session_id=channel.active_session_id,
            new_type=new_type,
            fork_reason=fork_reason,
            initiated_by=initiated_by,
        )
    except (SessionNotFoundError, ValueError) as e:
        abort(400, description=str(e))

    # Link the new child session back into the channel.
    ChannelRepository(get_db()).update_active_session(
        channel.channel_id, active_session_id=child.session_id
    )

    return redirect(
        url_for("sessions.show_session", session_id=child.session_id)
    )
