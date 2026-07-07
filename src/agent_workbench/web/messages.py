"""Messages blueprint — message-list views used by the session view.

This blueprint is intentionally thin. The session view page itself lives at
``/sessions/<id>``; this blueprint provides the partial-route helpers
(``messages.list``, the per-message row view, the polling-since endpoint,
and the Server-Sent Events stream) that the session template delegates to.
"""

from __future__ import annotations

import json
import sqlite3
import time

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, url_for

from agent_workbench.db import get_connection
from agent_workbench.services.participant_service import ParticipantService
from agent_workbench.services.routing_service import RoutingService
from agent_workbench.web.app import get_db, get_participant_service, get_routing_service

bp = Blueprint("messages", __name__, url_prefix="/messages")


def visible_messages_for_session(session_id: str):
    """Return chat-visible messages for a session.

    Internal dispatch hops are intentionally hidden from the human-facing
    session UI; the operator sees user/system/agent conversation rows, not
    the orchestration plumbing.
    """
    routing = get_routing_service()
    messages = routing.get_messages_by_session(session_id)
    return [m for m in messages if m.message_kind != "dispatch"]


def _load_participant_index(session_id: str, conn: sqlite3.Connection | None = None) -> dict:
    """Return a ``{binding_id: agent_name}`` index for the session.

    Used by both the SSE generator and the bubble template so the display
    name resolution is consistent regardless of the transport that
    rendered the row. When ``conn`` is provided we bypass ``flask.g`` and
    use that explicit connection — important for long-lived SSE streams,
    which must not depend on the request-scoped DB handle.
    """
    try:
        service = ParticipantService(conn) if conn is not None else get_participant_service()
        details = service.list_active_participant_details(session_id)
    except Exception:  # pragma: no cover - defensive
        return {}
    return {d["binding_id"]: d["agent_name"] for d in details if d.get("binding_id")}


@bp.route("/list/<session_id>")
def list_messages(session_id: str) -> str:
    """Return an HTML fragment with the visible messages for a session."""
    messages = visible_messages_for_session(session_id)
    return render_template(
        "message_list.html",
        session_id=session_id,
        messages=messages,
    )


@bp.route("/list/<session_id>/since")
def list_messages_since(session_id: str):
    """Return new visible messages plus the next polling cursor."""
    after = float(request.args.get("after", "0") or 0)
    messages = [m for m in visible_messages_for_session(session_id) if m.created_at > after]
    participants_index = _load_participant_index(session_id)
    html = "".join(
        render_template("message_row.html", message=m, participants=participants_index)
        for m in messages
    )
    next_after = after
    if messages:
        next_after = max(m.created_at for m in messages)
    return jsonify({"html": html, "next_after": next_after})


@bp.route("/stream/<session_id>")
def stream_messages(session_id: str):
    """Server-Sent Events stream for chat messages in a session.

    Behaviour:

    * If the client does not advertise ``text/event-stream`` in the
      ``Accept`` header, fall back to a 302 redirect to the existing
      JSON polling endpoint ``messages.list_messages_since``. The
      polling path stays untouched for the Flask test client and for
      any browser/proxy that cannot do SSE.
    * Otherwise the response is ``text/event-stream`` with a generator
      that polls the routed_messages table on a short interval and
      emits one SSE frame per new message.
    * Heartbeat / keep-alive comments are sent every
      ``WORKBENCH_SSE_HEARTBEAT`` seconds (default 25.0) so reverse
      proxies such as nginx do not close the connection on idle.
    * The poll interval is ``WORKBENCH_SSE_POLL`` (default 0.25 s).
    """
    accept = request.accept_mimetypes
    wants_sse = "text/event-stream" in accept

    if not wants_sse:
        # Fallback: redirect to the polling-since endpoint. We keep the
        # ``after`` query parameter so a client that *did* set it still
        # receives a useful response instead of the full backlog.
        target = url_for("messages.list_messages_since", session_id=session_id)
        after = request.args.get("after")
        if after:
            target = f"{target}?after={after}"
        return redirect(target, code=302)

    try:
        after = float(request.args.get("after", "0") or 0)
    except (TypeError, ValueError):
        after = 0.0

    # Verify the session exists up front; otherwise the generator would
    # loop forever emitting empty frames.
    from agent_workbench.models.session_extension import SessionExtensionRepository

    sess = SessionExtensionRepository(get_db()).get_by_id(session_id)
    if sess is None:
        abort(404, description=f"Session {session_id!r} not found")

    poll_interval = float(current_app.config.get("WORKBENCH_SSE_POLL", 0.25))
    heartbeat = float(current_app.config.get("WORKBENCH_SSE_HEARTBEAT", 25.0))
    # Cap one-sided sleeps so the teardown is responsive when the
    # WSGI server closes the connection.
    poll_interval = max(0.05, min(poll_interval, 1.0))
    heartbeat = max(poll_interval, heartbeat)

    # Snapshot app config / template objects before the generator runs so
    # the long-lived stream does not depend on the request context staying
    # alive while it is iterating.
    app = current_app._get_current_object()
    db_path = app.config["WORKBENCH_DB_PATH"]
    row_template = app.jinja_env.get_template("message_row.html")

    def gen():
        cursor = {"after": after}
        last_heartbeat = time.monotonic()
        # Open a dedicated DB connection for the lifetime of this
        # stream. The per-request ``g.db`` would be closed by
        # ``teardown_request`` once the response starts streaming.
        stream_conn = get_connection(db_path)
        try:
            # Comment frame signals the connection is open; clients
            # can use it to flip their UI to "live".
            yield b": connected\n\n"
            while True:
                try:
                    messages = [
                        m
                        for m in RoutingService(stream_conn).get_messages_by_session(
                            session_id
                        )
                        if m.message_kind != "dispatch" and m.created_at > cursor["after"]
                    ]
                    if messages:
                        participants_index = _load_participant_index(
                            session_id,
                            conn=stream_conn,
                        )
                        for m in messages:
                            cursor["after"] = max(cursor["after"], m.created_at)
                            html = row_template.render(
                                message=m,
                                participants=participants_index,
                            )
                            payload = {
                                "id": m.routed_message_id,
                                "created_at": m.created_at,
                                "html": html,
                            }
                            frame = (
                                f"id: {m.routed_message_id}\n"
                                f"event: message\n"
                                f"data: {json.dumps(payload)}\n\n"
                            )
                            yield frame.encode("utf-8")
                except Exception:  # pragma: no cover - defensive
                    # A transient DB/rendering error should not kill the
                    # stream; we simply skip this tick and try again.
                    pass
                now = time.monotonic()
                if now - last_heartbeat >= heartbeat:
                    yield b": keepalive\n\n"
                    last_heartbeat = now
                time.sleep(poll_interval)
        finally:
            try:
                stream_conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

    headers = {
        "Cache-Control": "no-cache",
        # nginx: disable proxy buffering for this response.
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(gen(), mimetype="text/event-stream", headers=headers)


@bp.route("/<message_id>")
def show_message(message_id: str) -> str:
    """Return a single routed message rendered as a row fragment."""
    conn = get_db()
    row = conn.execute(
        "SELECT routed_message_id, workspace_id, channel_id, session_id, "
        "source_type, source_id, target_type, target_id, message_kind, "
        "payload_ref, created_at "
        "FROM routed_messages WHERE routed_message_id = ?",
        (message_id,),
    ).fetchone()
    if row is None:
        abort(404, description=f"Message {message_id!r} not found")
    # Resolve participants so a /messages/<id> request has the same
    # naming context as the SSE/polling paths.
    participants_index = (
        _load_participant_index(row["session_id"]) if row["session_id"] else {}
    )
    return render_template("message_row.html", message=row, participants=participants_index)
