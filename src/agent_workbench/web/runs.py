"""Blueprint for the run detail panel.

The panel surfaces all required Work-run information (status, objective,
assigned agent, perspective, function, harness, transcript, tools/events,
artifacts, warnings, capability-aware controls).

Capability rules (per UI spec section 12 — "Honest capability rule")
--------------------------------------------------------------------
* Every control that is *unsupported* by the underlying harness adapter
  must be either hidden or rendered as a disabled button with a precise
  reason.  We render disabled buttons with a tooltip so the user can see
  *why* a control is unavailable.
* Never show fake universal controls.

Server-side enforcement
-----------------------
The ``stop`` and ``cancel`` POST endpoints also re-check the adapter's
declared capability.  A tampered client cannot bypass the UI's disabled
state — the server will 403 if the harness cannot perform the action.

Routes
------
* ``GET  /runs/<harness_run_id>``             — render run detail panel
* ``POST /runs/<harness_run_id>/stop``        — graceful stop (SIGTERM)
* ``POST /runs/<harness_run_id>/cancel``      — forceful cancel (SIGKILL)
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
import sqlite3

from agent_workbench.adapters.base import (
    AdapterCapabilities,
    BaseAdapter,
    HarnessNotReadyError,
    HarnessProcessError,
)
# Import the registry shim from the adapters package itself.  This
# avoids a circular import with ``services.run_service`` (which also
# needs to resolve adapter classes for its capability gate).
from agent_workbench.adapters import get_adapter_class as _get_adapter_class
from agent_workbench.models.agent_profile import AgentProfileRepository
from agent_workbench.models.agent_profile_binding import AgentProfileBindingRepository
from agent_workbench.models.artifact import ArtifactRepository
from agent_workbench.models.event_record import EventRecordRepository
from agent_workbench.models.harness_run import HarnessRun, HarnessRunRepository
from agent_workbench.models.task_spec import TaskSpecRepository
from agent_workbench.services.run_service import (
    HarnessUnavailableError,
    RunService,
    TaskSpecGateError,
)
from agent_workbench.services.transcript_service import TranscriptService
from agent_workbench.services.verification_service import (
    REPLAY_EQUIVALENCE_NOTE,
    VerificationService,
)
from agent_workbench.web.app import get_db

bp = Blueprint("runs", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    """Return the active per-request SQLite connection (see app.get_db)."""
    return get_db()


def _capabilities_for(run: HarnessRun) -> AdapterCapabilities:
    """Resolve the capability set for a run.

    Preference order:
    1. The capabilities JSON persisted on the HarnessRun row at start time.
    2. The live adapter class's declared capabilities.
    3. The empty default (all-False) if neither is available.

    Persisted capabilities win because they reflect what the *harness
    actually had* at the moment execution began, even if the adapter
    class has since been reconfigured.
    """
    if run.control_capabilities_json:
        try:
            data = json.loads(run.control_capabilities_json)
            return AdapterCapabilities(**data)
        except (json.JSONDecodeError, TypeError):
            pass

    cls = _get_adapter_class(run.harness_type)
    if cls is not None:
        # Touch the class to read its declared capabilities without
        # needing to instantiate against a connection.
        return cls.capabilities
    return AdapterCapabilities()


def _load_run_context(
    run: HarnessRun,
) -> Dict[str, Any]:
    """Pull together everything the run panel template needs."""
    conn = _conn()
    artifacts = ArtifactRepository(conn).list_by_session(run.session_id)
    events = EventRecordRepository(conn).list_by_harness_run(run.harness_run_id)

    # Persisted capabilities are the source of truth (see _capabilities_for).
    capabilities = _capabilities_for(run)

    # Resolve the bound agent profile (if any) so we can show
    # perspective / function / harness on the panel.
    perspective: Optional[str] = None
    function: Optional[str] = None
    profile_name: Optional[str] = None
    binding = (
        AgentProfileBindingRepository(conn).get_latest_for_session(run.session_id)
    )
    if binding is not None:
        profile = AgentProfileRepository(conn).get_by_id(binding.agent_profile_id)
        if profile is not None:
            profile_name = f"{profile.name}@{profile.version}"
            perspective = profile.perspective_ref
            function = profile.function_ref

    # Resolve the linked task spec for the objective line.
    objective: Optional[str] = None
    if run.task_spec_id:
        spec = TaskSpecRepository(conn).get_by_id(run.task_spec_id)
        if spec is not None:
            objective = spec.objective

    # Build the control surface.  Each control is annotated with
    # ``supported`` (bool) and ``reason`` (str).  Templates render
    # disabled controls with the reason as a tooltip.
    controls = _build_control_surface(capabilities)

    # Transcript/summary — the MVP run panel shows the raw stdout from
    # the harness run.  When the adapter has no transcript (e.g.
    # ``hermes`` mock with empty stdout) the panel still renders
    # gracefully with an explicit "no transcript" message.
    stdout, stderr = _load_transcript(run)
    warnings, errors = _extract_warnings_errors(events)

    # Lifecycle events from the durable ``harness_events`` table.
    lifecycle_events = TranscriptService().list_events(
        conn, harness_run_id=run.harness_run_id
    )

    # Tool invocation — if this run was triggered by a tool_call
    # (the AgentRuntimeService built a HarnessRun on behalf of a
    # tool_invocation), link back to the invocation for forensic
    # navigation.
    tool_invocation = None
    if run.tool_invocation_id:
        from agent_workbench.models.tool_invocation import (
            ToolInvocationRepository,
        )
        tool_invocation = ToolInvocationRepository(conn).get_by_id(
            run.tool_invocation_id
        )

    # Verification surface — Phase 7 cross-harness readiness projection.
    # The run panel surfaces verification_ready + replay_equivalence_note
    # so the user can see at-a-glance whether the run is ready to be
    # accepted as a verified product output.  We use the service-layer
    # projection (read-only) and never mutate state from the GET path.
    try:
        verification = VerificationService(conn).get_run_verification_surface(
            run.harness_run_id
        )
    except Exception:
        # Verification is a derivative view; a failure here must not 500
        # the run detail page.  We surface an explicit empty surface.
        verification = {
            "harness_run_id": run.harness_run_id,
            "session_id": run.session_id,
            "harness_type": run.harness_type,
            "status": run.status,
            "artifacts": [],
            "reviews": [],
            "replays": [],
            "latest_review_verdict": None,
            "replay_equivalence_note": REPLAY_EQUIVALENCE_NOTE,
            "verification_ready": False,
            "blockers": ["verification surface unavailable"],
        }

    return {
        "run": run,
        "artifacts": artifacts,
        "events": events,
        "lifecycle_events": lifecycle_events,
        "tool_invocation": tool_invocation,
        "capabilities": capabilities,
        "controls": controls,
        "objective": objective,
        "profile_name": profile_name,
        "perspective": perspective,
        "function": function,
        "stdout": stdout,
        "stderr": stderr,
        "warnings": warnings,
        "errors": errors,
        "verification": verification,
        "replay_equivalence_note": REPLAY_EQUIVALENCE_NOTE,
    }


def _build_control_surface(
    caps: AdapterCapabilities,
) -> List[Dict[str, Any]]:
    """Return the list of run controls with their support state.

    Each control is a dict with keys:
    - ``key``: short identifier used in templates
    - ``label``: human label
    - ``endpoint``: route name (Flask ``url_for``) when supported
    - ``method``: HTTP method
    - ``supported``: bool
    - ``reason``: precise reason when unsupported (else empty)
    """
    return [
        {
            "key": "stop",
            "label": "Stop",
            "endpoint": "runs.stop",
            "method": "POST",
            "supported": caps.can_stop,
            "reason": (
                "" if caps.can_stop
                else "Stop is not supported by this harness."
            ),
        },
        {
            "key": "cancel",
            "label": "Cancel",
            "endpoint": "runs.cancel",
            "method": "POST",
            "supported": caps.can_cancel,
            "reason": (
                "" if caps.can_cancel
                else "Cancel is not supported by this harness."
            ),
        },
        {
            "key": "pause",
            "label": "Pause",
            "endpoint": None,  # never routed in MVP
            "method": "POST",
            "supported": caps.can_pause,
            "reason": (
                "" if caps.can_pause
                else "Pause not supported by this harness"
            ),
        },
        {
            "key": "steer",
            "label": "Steer",
            "endpoint": None,  # never routed in MVP
            "method": "POST",
            "supported": caps.can_steer,
            "reason": (
                "" if caps.can_steer
                else "Steering not supported by this harness"
            ),
        },
    ]


def _load_transcript(run: HarnessRun) -> Tuple[str, str]:
    """Return ``(stdout, stderr)`` for the run, or empty strings.

    Reads from the *durable* transcript table (``harness_transcripts``),
    which survives server restarts.  If the table is empty (e.g. the
    run was created before migration 003 was applied, or the adapter
    never wrote any lines), we return empty strings — the in-memory
    adapter transcript is intentionally not consulted here because
    constructing a fresh adapter instance has no access to the
    original process's in-memory buffers.
    """
    conn = _conn()
    svc = TranscriptService()
    rows = svc.list(conn, harness_run_id=run.harness_run_id)
    if rows:
        out_lines = [r["content"] for r in rows if r["stream"] == "stdout"]
        err_lines = [r["content"] for r in rows if r["stream"] == "stderr"]
        return "\n".join(out_lines), "\n".join(err_lines)
    return "", ""


def _extract_warnings_errors(
    events: List[Any],
) -> Tuple[List[Any], List[Any]]:
    """Bucket events into warnings and errors based on their type tag."""
    warnings: List[Any] = []
    errors: List[Any] = []
    for ev in events:
        etype = (getattr(ev, "event_type", "") or "").lower()
        if "error" in etype:
            errors.append(ev)
        elif "warn" in etype:
            warnings.append(ev)
    return warnings, errors


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("/runs/<harness_run_id>", methods=["GET"])
def detail(harness_run_id: str):
    """Render the run detail panel."""
    repo = HarnessRunRepository(_conn())
    run = repo.get_by_id(harness_run_id)
    if run is None:
        abort(404, description=f"HarnessRun {harness_run_id} not found")
    ctx = _load_run_context(run)
    return render_template("run_panel.html", **ctx)


@bp.route("/runs/<harness_run_id>/stop", methods=["POST"])
def stop(harness_run_id: str):
    """Issue a graceful stop (SIGTERM) to the underlying harness."""
    repo = HarnessRunRepository(_conn())
    run = repo.get_by_id(harness_run_id)
    if run is None:
        abort(404, description=f"HarnessRun {harness_run_id} not found")

    caps = _capabilities_for(run)
    if not caps.can_stop:
        # Per UI spec section 12: never allow a control that the
        # adapter doesn't support, even via direct POST.
        abort(
            403,
            description="Stop is not supported by this harness.",
        )

    cls = _get_adapter_class(run.harness_type)
    if cls is None:
        abort(400, description=f"Unknown harness_type: {run.harness_type!r}")

    adapter: BaseAdapter = cls(_conn())
    try:
        adapter.stop(harness_run_id)
    except Exception as exc:  # noqa: BLE001
        flash(f"Stop failed: {exc}", "error")
        return redirect(url_for("runs.detail", harness_run_id=harness_run_id))

    flash("Stop signal sent.", "info")
    return redirect(url_for("runs.detail", harness_run_id=harness_run_id))


@bp.route("/runs/<harness_run_id>/cancel", methods=["POST"])
def cancel(harness_run_id: str):
    """Issue a forceful cancel (SIGKILL) to the underlying harness."""
    repo = HarnessRunRepository(_conn())
    run = repo.get_by_id(harness_run_id)
    if run is None:
        abort(404, description=f"HarnessRun {harness_run_id} not found")

    caps = _capabilities_for(run)
    if not caps.can_cancel:
        abort(
            403,
            description="Cancel is not supported by this harness.",
        )

    cls = _get_adapter_class(run.harness_type)
    if cls is None:
        abort(400, description=f"Unknown harness_type: {run.harness_type!r}")

    adapter: BaseAdapter = cls(_conn())
    try:
        adapter.cancel(harness_run_id)
    except Exception as exc:  # noqa: BLE001
        flash(f"Cancel failed: {exc}", "error")
        return redirect(url_for("runs.detail", harness_run_id=harness_run_id))

    flash("Cancel signal sent.", "warning")
    return redirect(url_for("runs.detail", harness_run_id=harness_run_id))


# ---------------------------------------------------------------------------
# UI start endpoints (aditive, capability-honest)
# ---------------------------------------------------------------------------
#
# These two routes are the missing link between the session view and the
# adapter layer.  ``POST /sessions/<id>/runs`` performs the dispatch
# through ``RunService.start_for_session`` and redirects to the run
# detail page on success.  ``GET /sessions/<id>/runs`` is the same
# data the session view template needs to render the Run-Liste; it is
# exposed as a separate endpoint so the existing session blueprint can
# include it as a fragment without re-implementing the query.
#
# Per the honest-capability rule (UI spec section 12), the only harness
# types the start endpoint will dispatch are ``shell``, ``opencode``,
# ``ssh``, and ``hermes``.  ``discussion`` is explicitly refused with a
# precise German reason — see :data:`run_service.DISABLED_HARNESS_TYPES`.


@bp.route("/sessions/<session_id>/runs", methods=["GET"])
def list_session_runs(session_id: str):
    """Return a small JSON list of harness runs attached to *session_id*.

    Exposed as JSON so the chat-poll pattern can be reused if the
    session view ever wants to refresh the run list without a full
    page reload.  The session view itself consumes the same data via
    the ``sessions.show_session`` route's context, so this endpoint
    is the dedicated read path for callers that don't want the
    full session view.
    """
    from agent_workbench.models.session_extension import (
        SessionExtensionRepository,
    )

    session = SessionExtensionRepository(_conn()).get_by_id(session_id)
    if session is None:
        abort(404, description=f"Session {session_id!r} not found")

    runs = RunService(_conn()).list_for_session(session_id)
    return {
        "session_id": session_id,
        "runs": [
            {
                "harness_run_id": r.harness_run_id,
                "harness_type": r.harness_type,
                "status": r.status,
                "task_spec_id": r.task_spec_id,
                "runtime_process_id": r.runtime_process_id,
                "runtime_remote_process_id": r.runtime_remote_process_id,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
            }
            for r in runs
        ],
    }


@bp.route("/sessions/<session_id>/runs", methods=["POST"])
def start_session_run(session_id: str):
    """Start a harness run for *session_id* via the chosen adapter.

    Form fields
    -----------
    * ``harness_type``  — required.  One of shell/opencode/ssh.
    * ``command``       — required for shell/opencode/ssh.
    * ``task_spec_id``  — optional.  When present and the session is
                          ``work``, the spec must be ``approved``;
                          the request must include ``force=1`` to
                          override.
    * ``remote_host``   — required for ``ssh``.
    * ``ssh_user``      — optional.
    * ``ssh_key``       — optional path to identity file.
    * ``force``         — ``"1"`` to bypass the TaskSpec approval gate.
    * ``agent_profile_id`` / ``participant_id`` — stored in the
                          artifact_summary for traceability; not yet
                          wired into the adapter contract.
    """
    harness_type = (request.form.get("harness_type") or "").strip()
    command = (request.form.get("command") or "").strip()
    task_spec_id = (request.form.get("task_spec_id") or "").strip() or None
    remote_host = (request.form.get("remote_host") or "").strip() or None
    ssh_user = (request.form.get("ssh_user") or "").strip() or None
    ssh_key = (request.form.get("ssh_key") or "").strip() or None
    force = (request.form.get("force") or "").strip() in ("1", "true", "on")
    agent_profile_id = (request.form.get("agent_profile_id") or "").strip() or None
    participant_id = (request.form.get("participant_id") or "").strip() or None

    if not harness_type:
        flash("Harness-Typ ist erforderlich.", "error")
        return redirect(url_for("sessions.show_session", session_id=session_id))

    adapter_kwargs: Dict[str, Any] = {}
    if remote_host:
        adapter_kwargs["remote_host"] = remote_host
    if ssh_user:
        adapter_kwargs["ssh_user"] = ssh_user
    if ssh_key:
        adapter_kwargs["ssh_key"] = ssh_key

    try:
        run = RunService(_conn()).start_for_session(
            session_id=session_id,
            harness_type=harness_type,
            command=command,
            task_spec_id=task_spec_id,
            agent_profile_id=agent_profile_id,
            participant_id=participant_id,
            force=force,
            **adapter_kwargs,
        )
    except TaskSpecGateError as exc:
        flash(str(exc), "error")
        return redirect(url_for("sessions.show_session", session_id=session_id))
    except HarnessUnavailableError as exc:
        flash(str(exc), "error")
        return redirect(url_for("sessions.show_session", session_id=session_id))
    except HarnessNotReadyError as exc:
        flash(f"Run konnte nicht gestartet werden: {exc}", "error")
        return redirect(url_for("sessions.show_session", session_id=session_id))
    except ValueError as exc:
        flash(f"Ungültiger Run-Aufruf: {exc}", "error")
        return redirect(url_for("sessions.show_session", session_id=session_id))
    except Exception as exc:  # noqa: BLE001
        # Last-resort: the UI must never 500 on a user-initiated start.
        flash(f"Unerwarteter Fehler beim Run-Start: {exc}", "error")
        return redirect(url_for("sessions.show_session", session_id=session_id))

    flash(
        f"Run gestartet: {harness_type!r} → {run.harness_run_id[:12]}…",
        "success",
    )
    return redirect(url_for("runs.detail", harness_run_id=run.harness_run_id))
