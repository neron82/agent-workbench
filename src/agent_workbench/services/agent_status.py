"""AgentStatusTracker — shared in-memory state for agent progress.

This module provides a thread-safe singleton that tracks what each agent
is currently doing during a tool-calling loop. The runtime updates it
between iterations; the web layer reads it to render the "working" bubble
and the detail panel.

Stop mechanism: each running agent has a threading.Event. When set,
the runtime checks it between iterations and exits cleanly.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentStep:
    """One step in the agent's tool-calling loop."""

    iteration: int
    tool_name: str
    tool_arguments: Dict[str, Any]
    tool_result: Optional[str] = None
    status: str = "running"  # running | completed | failed
    started_at: float = 0.0
    completed_at: Optional[float] = None


@dataclass
class AgentStatus:
    """Current status of one agent in one session."""

    session_id: str
    agent_name: str
    status: str = "idle"  # idle | working | completed | stopped | error
    current_step: Optional[AgentStep] = None
    steps: List[AgentStep] = field(default_factory=list)
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    iteration_count: int = 0


class AgentStatusTracker:
    """Thread-safe singleton for agent progress tracking."""

    _instance: Optional["AgentStatusTracker"] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._status: Dict[str, AgentStatus] = {}
        self._stop_events: Dict[str, threading.Event] = {}
        self._data_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "AgentStatusTracker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _key(self, session_id: str, agent_name: str) -> str:
        return f"{session_id}:{agent_name}"

    def start_agent(self, session_id: str, agent_name: str) -> None:
        """Mark an agent as working."""
        key = self._key(session_id, agent_name)
        with self._data_lock:
            self._status[key] = AgentStatus(
                session_id=session_id,
                agent_name=agent_name,
                status="working",
                started_at=time.time(),
                steps=[],
                iteration_count=0,
            )
            self._stop_events[key] = threading.Event()

    def start_step(
        self,
        session_id: str,
        agent_name: str,
        iteration: int,
        tool_name: str,
        tool_arguments: Dict[str, Any],
    ) -> None:
        """Record the start of a tool call."""
        key = self._key(session_id, agent_name)
        with self._data_lock:
            status = self._status.get(key)
            if status is None:
                return
            step = AgentStep(
                iteration=iteration,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
                status="running",
                started_at=time.time(),
            )
            status.current_step = step
            status.steps.append(step)
            status.iteration_count = iteration

    def complete_step(
        self,
        session_id: str,
        agent_name: str,
        result: str,
        failed: bool = False,
    ) -> None:
        """Record the result of a tool call."""
        key = self._key(session_id, agent_name)
        with self._data_lock:
            status = self._status.get(key)
            if status is None or status.current_step is None:
                return
            status.current_step.tool_result = result
            status.current_step.status = "failed" if failed else "completed"
            status.current_step.completed_at = time.time()

    def complete_agent(
        self,
        session_id: str,
        agent_name: str,
        error: Optional[str] = None,
    ) -> None:
        """Mark an agent as done.

        Preserves an existing 'stopped' status so the UI can distinguish
        user-initiated stops from normal completion.
        """
        key = self._key(session_id, agent_name)
        with self._data_lock:
            status = self._status.get(key)
            if status is None:
                return
            if status.status != "stopped":
                status.status = "error" if error else "completed"
            status.error = error
            status.completed_at = time.time()
            status.current_step = None
            self._stop_events.pop(key, None)

    def get_status(
        self, session_id: str, agent_name: str
    ) -> Optional[AgentStatus]:
        """Return the current status for an agent."""
        key = self._key(session_id, agent_name)
        with self._data_lock:
            return self._status.get(key)

    def get_session_statuses(
        self, session_id: str
    ) -> List[AgentStatus]:
        """Return statuses for all agents in a session."""
        prefix = f"{session_id}:"
        with self._data_lock:
            return [
                s for k, s in self._status.items()
                if k.startswith(prefix)
            ]

    def should_stop(self, session_id: str, agent_name: str) -> bool:
        """Check if the agent should stop (non-blocking)."""
        key = self._key(session_id, agent_name)
        with self._data_lock:
            event = self._stop_events.get(key)
            if event is None:
                return False
            return event.is_set()

    def stop_agent(self, session_id: str, agent_name: str) -> bool:
        """Signal an agent to stop. Returns True if the agent was running."""
        key = self._key(session_id, agent_name)
        with self._data_lock:
            event = self._stop_events.get(key)
            if event is None:
                return False
            event.set()
            status = self._status.get(key)
            if status is not None:
                status.status = "stopped"
            return True

    def cleanup_session(self, session_id: str) -> None:
        """Remove all status entries for a session."""
        prefix = f"{session_id}:"
        with self._data_lock:
            keys = [k for k in self._status if k.startswith(prefix)]
            for k in keys:
                self._status.pop(k, None)
                self._stop_events.pop(k, None)
