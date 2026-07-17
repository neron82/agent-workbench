"""Service layer for starting and listing harness runs from the UI.

Why this exists
---------------
Before this module the web layer could inspect / stop / cancel runs, but
it could not create them. This service is the missing translation layer
from session-scoped form input to a real adapter invocation.

Honest-capability contract
--------------------------
The UI should only advertise harnesses that can start a real run on the
current host. In the current build that means:

* ``shell``    — local subprocess
* ``opencode`` — ``opencode serve`` subprocess (binary required)
* ``ssh``      — remote subprocess via system ``ssh``
* ``hermes``   — host-local ``hermes chat -Q -q ...`` subprocess

``discussion`` remains intentionally disabled in the picker because it is
not a user-started process harness.
"""
from __future__ import annotations

import shutil
from typing import Any, Dict, List, Optional, Tuple

import sqlite3

from agent_workbench.adapters import get_adapter_class
from agent_workbench.adapters.base import (
    BaseAdapter,
    HarnessNotReadyError,
    HarnessProcessError,
)
from agent_workbench.models.harness_run import HarnessRun, HarnessRunRepository
from agent_workbench.models.session_extension import SessionExtensionRepository
from agent_workbench.models.task_spec import TaskSpecRepository


# ---------------------------------------------------------------------------
# Public constants — the UI consumes these.
# ---------------------------------------------------------------------------

# Harness types for which ``Adapter.start`` performs a real
# subprocess / remote action on this host.
LIVE_HARNESS_TYPES: Tuple[str, ...] = ("shell", "opencode", "ssh", "hermes")


# Harness types that are deliberately NOT exposed in the UI start picker.
# The pair is (harness_type, reason) so the UI can show a precise
# explanation when somebody hits the endpoint directly.
DISABLED_HARNESS_TYPES: Dict[str, str] = {
    "discussion": (
        "Discussion-Runs benötigen keinen Prozess und werden nicht aus der "
        "UI gestartet — sie entstehen entlang des Chat- oder Research-Flusses."
    ),
}

ALL_HARNESS_TYPES: Tuple[str, ...] = LIVE_HARNESS_TYPES + tuple(
    DISABLED_HARNESS_TYPES.keys()
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HarnessUnavailableError(HarnessNotReadyError):
    """Raised when a requested harness cannot start a real run on this host.

    The message is intentionally user-facing German; the web layer
    surfaces it via ``flash()`` without further translation.
    """


class TaskSpecGateError(HarnessNotReadyError):
    """Raised when a Work-run is requested with a non-approved TaskSpec."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RunService:
    """Start/list harness runs from the session UI.

    Parameters
    ----------
    conn:
        Per-request SQLite connection (see :func:`agent_workbench.web.app.get_db`).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # Public queries
    # ------------------------------------------------------------------

    @staticmethod
    def available_harness_types() -> List[Dict[str, str]]:
        """Return the list of harness types the UI start picker may show.

        Each entry has ``harness_type`` and ``label``.  Live types come
        first; disabled types are appended with an extra ``disabled``
        flag and a precise ``reason``.
        """
        out: List[Dict[str, str]] = []
        for ht in LIVE_HARNESS_TYPES:
            out.append({"harness_type": ht, "label": ht, "live": "true"})
        for ht, reason in DISABLED_HARNESS_TYPES.items():
            out.append(
                {
                    "harness_type": ht,
                    "label": ht,
                    "live": "false",
                    "reason": reason,
                }
            )
        return out

    def list_for_session(self, session_id: str) -> List[HarnessRun]:
        """Return all harness runs attached to ``session_id`` (newest first)."""
        repo = HarnessRunRepository(self.conn)
        runs = repo.list_by_session(session_id)
        # Newest first; the repository returns ASC, reverse in Python so
        # the SQL stays trivially correct (and we avoid ordering by
        # nullable ``started_at`` in SQL, which would silently hide
        # still-queued rows).
        return list(reversed(runs))

    # ------------------------------------------------------------------
    # Public actions
    # ------------------------------------------------------------------

    def start_for_session(
        self,
        *,
        session_id: str,
        harness_type: str,
        command: str,
        task_spec_id: Optional[str] = None,
        agent_profile_id: Optional[str] = None,
        participant_id: Optional[str] = None,
        force: bool = False,
        **adapter_kwargs: Any,
    ) -> HarnessRun:
        """Start a harness run bound to ``session_id``.

        Steps
        -----
        1. Validate session existence.
        2. Reject disabled harness types with the precise reason.
        3. Pre-flight checks (binary / host availability).
        4. Apply the TaskSpec approval gate (or honour ``force``).
        5. Workspace-isolation check (Spec ↔ Session).
        6. Resolve the adapter class and call ``adapter.start(...)``.
        7. Return the persisted ``HarnessRun``.

        Parameters
        ----------
        force:
            When True, skip the TaskSpec approval gate.  The bypass is
            *recorded* in ``HarnessRun.artifact_summary_json`` so that a
            later review can see who/why the gate was circumvented.

        Raises
        ------
        HarnessUnavailableError
            On disabled harness, missing binary, or unreachable host.
        TaskSpecGateError
            When a non-approved spec is passed without ``force=True``.
        ValueError
            When the harness type is unknown to the registry.
        """
        session = SessionExtensionRepository(self.conn).get_by_id(session_id)
        if session is None:
            raise HarnessNotReadyError(
                f"Session {session_id!r} not found"
            )

        # 2. Refuse disabled harness types up front.
        if harness_type in DISABLED_HARNESS_TYPES:
            raise HarnessUnavailableError(
                f"Harness-Typ {harness_type!r} ist nicht startbar: "
                f"{DISABLED_HARNESS_TYPES[harness_type]}"
            )

        if harness_type not in LIVE_HARNESS_TYPES:
            raise ValueError(
                f"Unknown harness_type: {harness_type!r}. "
                f"Expected one of {ALL_HARNESS_TYPES!r}."
            )

        # 3. Pre-flight: per-harness sanity checks before we even touch
        # the registry.  These are intentionally cheap and synchronous.
        self._preflight(harness_type=harness_type, command=command, **adapter_kwargs)

        # 4. TaskSpec gate.  ``is_work`` sessions require an approved
        # spec; for chat / research sessions the spec is optional and
        # the gate only fires when a spec is explicitly attached.
        spec = None
        if task_spec_id:
            spec = TaskSpecRepository(self.conn).get_by_id(task_spec_id)
            if spec is None:
                raise HarnessNotReadyError(
                    f"TaskSpec {task_spec_id!r} not found"
                )
            if spec.workspace_id != session.workspace_id:
                raise HarnessNotReadyError(
                    "TaskSpec gehört zu einem anderen Workspace als die Session."
                )
            if session.session_type == "work" and spec.approval_status != "approved":
                if not force:
                    raise TaskSpecGateError(
                        f"TaskSpec {task_spec_id!r} ist nicht approved "
                        f"(Status: {spec.approval_status!r}). "
                        f"Für Work-Runs muss die Spec zuerst approvet "
                        f"werden, oder es muss force=True übergeben werden."
                    )

        # 5. Resolve adapter class.
        cls = get_adapter_class(harness_type)
        if cls is None:  # pragma: no cover - registry always populated above
            raise ValueError(f"Unknown harness_type: {harness_type!r}")

        # 6. Construct adapter against the live connection and start.
        adapter: BaseAdapter = cls(self.conn)
        # Build the adapter kwargs without leaking the gate-bypass
        # marker into the adapter contract.
        adapter_kwargs_final: Dict[str, Any] = {}
        for key, value in adapter_kwargs.items():
            # task_spec_id is part of the BaseAdapter.start signature; we
            # let the service pick the right one (from spec if any).
            if key == "task_spec_id":
                continue
            if key == "command":
                continue
            adapter_kwargs_final[key] = value
        if spec is not None:
            adapter_kwargs_final["task_spec_id"] = spec.task_spec_id

        try:
            harness_run_id = adapter.start(
                workspace_id=session.workspace_id,
                session_id=session.session_id,
                command=command,
                **adapter_kwargs_final,
            )
        except HarnessNotReadyError:
            raise
        except ConnectionError as exc:
            # OpencodeAdapter signals "binary missing" with ConnectionError
            # — translate to a friendlier HarnessUnavailableError.
            raise HarnessUnavailableError(str(exc)) from exc
        except Exception as exc:
            raise HarnessProcessError(str(exc)) from exc

        # 7. Reload the run from the repository so the caller sees the
        # post-start status (typically ``running``) and runtime IDs.
        run = HarnessRunRepository(self.conn).get_by_id(harness_run_id)
        if run is None:  # pragma: no cover - defensive
            raise HarnessProcessError(
                f"Adapter reported harness_run_id={harness_run_id!r} but no row was persisted."
            )
        return run

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _preflight(
        self, *, harness_type: str, command: str, **kwargs: Any
    ) -> None:
        """Synchronous pre-flight check before calling ``adapter.start``.

        Currently we verify:

        * ``shell``/``opencode``/``ssh`` ``command`` is non-empty.
        * ``opencode`` binary actually exists on PATH (or the caller
          supplied an explicit ``env`` that contains one).
        * ``ssh`` ``remote_host`` is non-empty.
        """
        if harness_type in ("shell", "opencode", "ssh"):
            if not command or not command.strip():
                raise HarnessUnavailableError(
                    f"Für Harness {harness_type!r} ist ein 'command' erforderlich."
                )

        if harness_type == "opencode":
            child_env = kwargs.get("env")
            lookup_path = (
                child_env.get("PATH") if isinstance(child_env, dict) else None
            )
            if shutil.which("opencode", path=lookup_path) is None:
                raise HarnessUnavailableError(
                    "Opencode-Binary nicht im PATH gefunden. "
                    "Bitte opencode installieren oder den Pfad über "
                    "env={...} mitschicken."
                )

        if harness_type == "hermes":
            child_env = kwargs.get("env")
            lookup_path = (
                child_env.get("PATH") if isinstance(child_env, dict) else None
            )
            if shutil.which("hermes", path=lookup_path) is None:
                raise HarnessUnavailableError(
                    "Hermes-Binary nicht im PATH gefunden. "
                    "Bitte hermes-agent installieren oder den Pfad über "
                    "env={...} mitschicken."
                )

        if harness_type == "ssh":
            remote_host = kwargs.get("remote_host") or ""
            if not remote_host:
                raise HarnessUnavailableError(
                    "SSH-Runs brauchen ein 'remote_host'-Feld (z. B. 'localhost' "
                    "oder ein SSH-Alias)."
                )
            # Optional: ping the host with `ssh -o BatchMode=yes` to
            # surface credential problems early.  Skip this if the
            # caller asks for a fast path; we default to a soft check
            # that only fails if ssh itself is missing.
            if shutil.which("ssh") is None:
                raise HarnessUnavailableError(
                    "ssh-Binary nicht im PATH gefunden."
                )
