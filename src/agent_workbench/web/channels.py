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
    g,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)

from agent_workbench.models.channel import CHANNEL_KINDS, ChannelRepository
from agent_workbench.models.session_extension import (
    SESSION_STATUSES,
    SESSION_TYPES,
    SessionExtensionRepository,
)
from agent_workbench.models.session_label import SessionLabelRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.session_service import (
    SessionNotFoundError,
)
from agent_workbench.services.profile_service import ProfileService
from agent_workbench.services.team_service import (
    DuplicateTeamMemberError,
    DuplicateTeamNameError,
    TeamMemberNotFoundError,
    TeamNotFoundError,
    TeamService,
)
from agent_workbench.web.app import (
    get_db,
    get_orchestrator,
    get_participant_service,
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
    ws_id = request.values.get("workspace_id") or flask_session.get("workbench_workspace_id")
    repo = WorkspaceRepository(get_db())
    if ws_id:
        selected = repo.get_by_id(ws_id)
        if selected is not None:
            flask_session["workbench_workspace_id"] = selected.workspace_id
            return selected.workspace_id

    default = repo.get_default(tenant_id="default")
    if default is not None:
        flask_session["workbench_workspace_id"] = default.workspace_id
        return default.workspace_id
    # Fall back to the most recent workspace if no explicit default exists.
    all_ws = repo.list_all()
    if all_ws:
        flask_session["workbench_workspace_id"] = all_ws[0].workspace_id
        return all_ws[0].workspace_id

    # First-run bootstrap: create the default single-user workspace that
    # the MVP assumes exists, so the web UI renders usable channel lists
    # and creation forms on first launch.
    created = repo.create(
        tenant_id="default",
        name="Default Workspace",
        is_default=True,
    )
    flask_session["workbench_workspace_id"] = created.workspace_id
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


def _resolve_identity():
    """Return the browser-local identity established by the app hook."""
    user = getattr(g, "current_user", None)
    if user is None:
        return "", "User"
    return user.user_id, user.display_name


def _resolve_session_labels(workspace_id: str) -> tuple[dict, dict, dict]:
    """Return (label_display, label_colors, label_descriptions) dicts indexed by label name."""
    conn = get_db()
    repo = SessionLabelRepository(conn)
    labels = repo.list_by_workspace(workspace_id)
    display = {}
    colors = {}
    descriptions = {}
    for lbl in labels:
        display[lbl.name] = lbl.display_name or lbl.name
        colors[lbl.name] = lbl.color
        descriptions[lbl.name] = lbl.description
    return display, colors, descriptions


def _resolve_participant_counts(workspace_id: str, session_ids: list[str]) -> dict[str, int]:
    """Return a {session_id: participant_count} dict for the given session IDs."""
    if not session_ids:
        return {}
    conn = get_db()
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        f"SELECT session_id, COUNT(*) AS cnt FROM session_participants "
        f"WHERE session_id IN ({placeholders}) AND removed_at IS NULL GROUP BY session_id",
        session_ids,
    ).fetchall()
    return {r["session_id"]: r["cnt"] for r in rows}


def _resolve_working_counts(workspace_id: str, session_ids: list[str]) -> dict[str, int]:
    """Return a {session_id: working_agent_count} from the live status tracker."""
    from agent_workbench.services.agent_status import AgentStatusTracker
    tracker = AgentStatusTracker.get_instance()
    counts = {}
    for sid in session_ids:
        statuses = tracker.get_session_statuses(sid)
        working = sum(1 for s in statuses if s.status == "working")
        if working > 0:
            counts[sid] = working
    return counts


def _session_browser(workspace_id: str) -> dict:
    """Build the URL-driven session browser for one workspace."""
    conn = get_db()
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip()
    session_type = (request.args.get("type") or "all").strip()
    sort = (request.args.get("sort") or "updated").strip()
    view = request.args.get("view", "cards")
    view = view if view in {"cards", "table"} else "cards"
    try:
        page_size = min(max(int(request.args.get("per_page", "12")), 1), 50)
    except ValueError:
        page_size = 12
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1

    clauses = ["s.workspace_id = ?"]
    params: list[object] = [workspace_id]
    if status in SESSION_STATUSES:
        clauses.append("s.status = ?")
        params.append(status)
    else:
        status = "all"
    if session_type in SESSION_TYPES:
        clauses.append("s.session_type = ?")
        params.append(session_type)
    else:
        session_type = "all"
    if q:
        like = f"%{q.lower()}%"
        clauses.append(
            "(LOWER(COALESCE(s.title, '')) LIKE ? OR EXISTS ("
            "SELECT 1 FROM routed_messages search_m "
            "WHERE search_m.session_id = s.session_id "
            "AND LOWER(COALESCE(search_m.payload_ref, '')) LIKE ?))"
        )
        params.extend([like, like])

    order_by = {
        "created": "s.created_at DESC",
        "title": "LOWER(COALESCE(s.title, '')) ASC, s.created_at DESC",
        "message_count": "message_count DESC, s.created_at DESC",
        "updated": "COALESCE(MAX(CASE WHEN r.message_kind NOT IN ('dispatch', 'agent_work') THEN r.created_at END), s.created_at) DESC",
    }.get(sort, "COALESCE(MAX(CASE WHEN r.message_kind NOT IN ('dispatch', 'agent_work') THEN r.created_at END), s.created_at) DESC")
    sort = sort if sort in {"created", "title", "message_count", "updated"} else "updated"

    where_sql = " AND ".join(clauses)
    rows = conn.execute(
        "SELECT s.session_id, s.workspace_id, s.session_type, s.status, s.title, "
        "s.created_at, COUNT(CASE WHEN r.message_kind NOT IN ('dispatch', 'agent_work') "
        "THEN 1 END) AS message_count, "
        "(SELECT payload_ref FROM routed_messages latest_m "
        "WHERE latest_m.session_id = s.session_id "
        "AND latest_m.message_kind NOT IN ('dispatch', 'agent_work') "
        "ORDER BY latest_m.created_at DESC LIMIT 1) AS last_payload_ref "
        "FROM session_extensions s LEFT JOIN routed_messages r "
        "ON r.session_id = s.session_id "
        f"WHERE {where_sql} GROUP BY s.session_id ORDER BY {order_by}",
        params,
    ).fetchall()
    total = len(rows)
    pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, pages)
    start = (page - 1) * page_size
    cards = [dict(row) for row in rows[start:start + page_size]]
    return {
        "cards": cards,
        "total": total,
        "page": page,
        "pages": pages,
        "page_size": page_size,
        "q": q,
        "status": status,
        "session_type": session_type,
        "sort": sort,
        "view": view,
    }


def _workspace_or_404(workspace_id: str):
    workspace = WorkspaceRepository(get_db()).get_by_id(workspace_id)
    if workspace is None:
        abort(404, description=f"Workspace {workspace_id!r} not found")
    return workspace


@bp.route("/workspaces", methods=["POST"])
def create_workspace():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Workspace name cannot be empty.", "error")
        return redirect(url_for("channels.index"))
    if len(name) > 120:
        flash("Workspace name must be 120 characters or fewer.", "error")
        return redirect(url_for("channels.index"))
    workspace = WorkspaceRepository(get_db()).create(
        tenant_id="default", name=name,
    )
    flask_session["workbench_workspace_id"] = workspace.workspace_id
    flash(f"Workspace {name!r} created.", "success")
    return redirect(url_for("channels.index", workspace_id=workspace.workspace_id))


@bp.route("/workspaces/<workspace_id>/settings")
def workspace_settings(workspace_id: str):
    workspace = _workspace_or_404(workspace_id)
    flask_session["workbench_workspace_id"] = workspace.workspace_id
    conn = get_db()
    team_service = TeamService(conn)
    teams = team_service.list_teams(workspace_id)
    team_members = {}
    for team in teams:
        rows = conn.execute(
            "SELECT atm.*, ap.name AS agent_name FROM agent_team_members atm "
            "JOIN agent_profiles ap ON ap.agent_profile_id = atm.agent_profile_id "
            "WHERE atm.team_id = ? ORDER BY atm.sort_order, atm.created_at",
            (team.team_id,),
        ).fetchall()
        team_members[team.team_id] = [dict(row) for row in rows]
    return render_template(
        "workspace_settings.html",
        workspace=workspace,
        workspaces=WorkspaceRepository(conn).list_all(),
        teams=teams,
        team_members=team_members,
        available_agents=ProfileService(conn).list_latest_profiles(),
    )


@bp.route("/workspaces/<workspace_id>/teams", methods=["POST"])
def create_team(workspace_id: str):
    _workspace_or_404(workspace_id)
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not name:
        flash("Team name cannot be empty.", "error")
    else:
        try:
            TeamService(get_db()).create_team(
                workspace_id=workspace_id, name=name, description=description
            )
            flash(f"Team {name!r} created.", "success")
        except DuplicateTeamNameError as exc:
            flash(str(exc), "error")
    return redirect(url_for("channels.workspace_settings", workspace_id=workspace_id))


@bp.route("/workspaces/<workspace_id>/teams/<team_id>/members", methods=["POST"])
def add_team_member(workspace_id: str, team_id: str):
    _workspace_or_404(workspace_id)
    service = TeamService(get_db())
    team = service.get_team(team_id)
    if team is None or team.workspace_id != workspace_id:
        abort(404)
    profile_id = (request.form.get("agent_profile_id") or "").strip()
    try:
        sort_order = int(request.form.get("sort_order") or 0)
    except ValueError:
        sort_order = 0
    try:
        service.add_member(
            team_id=team_id,
            agent_profile_id=profile_id,
            role_label=(request.form.get("role_label") or "member").strip() or "member",
            sort_order=sort_order,
        )
        flash("Agent added to team.", "success")
    except (TeamNotFoundError, TeamMemberNotFoundError, DuplicateTeamMemberError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("channels.workspace_settings", workspace_id=workspace_id))


@bp.route(
    "/workspaces/<workspace_id>/teams/<team_id>/members/<member_id>/remove",
    methods=["POST"],
)
def remove_team_member(workspace_id: str, team_id: str, member_id: str):
    _workspace_or_404(workspace_id)
    service = TeamService(get_db())
    team = service.get_team(team_id)
    member = service.members.get_by_id(member_id)
    if (
        team is None
        or team.workspace_id != workspace_id
        or member is None
        or member.team_id != team_id
    ):
        abort(404)
    service.remove_member(member_id)
    flash("Agent removed from team.", "success")
    return redirect(url_for("channels.workspace_settings", workspace_id=workspace_id))


@bp.route("/workspaces/<workspace_id>/teams/<team_id>/delete", methods=["POST"])
def delete_team(workspace_id: str, team_id: str):
    _workspace_or_404(workspace_id)
    service = TeamService(get_db())
    team = service.get_team(team_id)
    if team is None or team.workspace_id != workspace_id:
        abort(404)
    service.delete_team(team_id)
    flash(f"Team {team.name!r} deleted.", "success")
    return redirect(url_for("channels.workspace_settings", workspace_id=workspace_id))


@bp.route("/workspaces/<workspace_id>/rename", methods=["POST"])
def rename_workspace(workspace_id: str):
    workspace = _workspace_or_404(workspace_id)
    name = (request.form.get("name") or "").strip()
    if not name or len(name) > 120:
        flash("Workspace name must be between 1 and 120 characters.", "error")
    else:
        WorkspaceRepository(get_db()).update(workspace.workspace_id, name=name)
        flash("Workspace renamed.", "success")
    return redirect(url_for("channels.workspace_settings", workspace_id=workspace_id))


@bp.route("/workspaces/<workspace_id>/delete", methods=["POST"])
def delete_workspace(workspace_id: str):
    workspace = _workspace_or_404(workspace_id)
    conn = get_db()
    counts = conn.execute(
        "SELECT COUNT(*) AS sessions FROM session_extensions WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    if counts["sessions"]:
        flash("Workspace must be empty before it can be deleted.", "error")
        return redirect(url_for("channels.workspace_settings", workspace_id=workspace_id))
    # Empty means no user-visible sessions.  Detached channels, built-in
    # metadata, and abandoned operational records are workspace-owned
    # internals and must not make deletion impossible.
    run_ids = [
        row["harness_run_id"]
        for row in conn.execute(
            "SELECT harness_run_id FROM harness_runs WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
    ]
    try:
        # event_records has no workspace_id of its own.  Remove message-only
        # audit rows through their workspace-scoped routed message before the
        # routed_messages parent rows are deleted.
        conn.execute(
            "DELETE FROM event_records WHERE routed_message_id IN ("
            "SELECT routed_message_id FROM routed_messages WHERE workspace_id = ?"
            ")",
            (workspace_id,),
        )
        for run_id in run_ids:
            for table in (
                "permission_requests",
                "replay_records",
                "harness_transcripts",
                "harness_events",
                "tool_invocations",
                "event_records",
            ):
                column = "source_harness_run_id" if table == "replay_records" else "harness_run_id"
                conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (run_id,))
        for table in (
            "review_records",
            "project_assets",
            "artifacts",
            "harness_runs",
            "routed_messages",
            "task_specs",
            "session_participants",
            "session_labels",
            "agent_teams",
            "channels",
            "cross_harness_permissions",
            "tool_invocations",
        ):
            conn.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (workspace_id,))
        conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (workspace_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if flask_session.get("workbench_workspace_id") == workspace_id:
        flask_session.pop("workbench_workspace_id", None)
    flash(f"Workspace {workspace.name!r} deleted.", "success")
    return redirect(url_for("channels.index"))


@bp.route("/")
def index():
    """Landing page — project dashboard with session cards."""
    workspace_id = _resolve_workspace_id()
    if not workspace_id:
        return render_template(
            "landing.html",
            workspace_id=None,
            workspace_name=None,
            session_browser={
                "cards": [], "total": 0, "page": 1, "pages": 1,
                "page_size": 12, "q": "", "status": "all",
                "session_type": "all", "sort": "updated", "view": "cards",
            },
            session_label_display={},
            session_label_colors={},
            session_label_descriptions={},
            participant_counts={},
            working_counts={},
            current_user_id="web-user",
            current_user_display="User",
        )

    # Resolve workspace name
    ws = WorkspaceRepository(get_db()).get_by_id(workspace_id)

    # Session browser across all types, driven by URL query parameters.
    browser = _session_browser(workspace_id)

    # Session labels
    label_display, label_colors, label_descriptions = _resolve_session_labels(workspace_id)

    # Participant counts for the current page only.
    session_ids = [card["session_id"] for card in browser["cards"]]
    p_counts = _resolve_participant_counts(workspace_id, session_ids)
    w_counts = _resolve_working_counts(workspace_id, session_ids)

    # Identity
    uid, display = _resolve_identity()

    return render_template(
        "landing.html",
        workspace_id=workspace_id,
        workspace_name=ws.name if ws else None,
        session_browser=browser,
        session_label_display=label_display,
        session_label_colors=label_colors,
        session_label_descriptions=label_descriptions,
        participant_counts=p_counts,
        working_counts=w_counts,
        current_user_id=uid,
        current_user_display=display,
    )


@bp.route("/sessions/type/<session_type>")
def list_by_type(session_type: str):
    """Redirect to the canonical session browser (channels.index) with type filter.

    Preserves the resolved or explicit workspace ID.  Invalid session types
    still abort with 400 so bookmarked URLs don't silently degrade.
    """
    if session_type not in ("chat", "research", "work"):
        abort(400, description=f"Invalid session type: {session_type!r}")

    workspace_id = _resolve_workspace_id()
    return redirect(
        url_for("channels.index", workspace_id=workspace_id, type=session_type)
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
