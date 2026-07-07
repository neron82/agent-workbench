"""Base harness adapter interface."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------

class HarnessAdapterError(Exception):
    """Base exception for harness adapter errors."""


class HarnessNotReadyError(HarnessAdapterError):
    """Adapter is not in a state to perform the requested action."""


class HarnessProcessError(HarnessAdapterError):
    """Error communicating with the underlying process."""


# ------------------------------------------------------------------
# Capability descriptor
# ------------------------------------------------------------------

@dataclass(frozen=True)
class AdapterCapabilities:
    """Immutable capability set declared by a concrete adapter."""

    can_stop: bool = False
    can_cancel: bool = False
    can_pause: bool = False
    can_steer: bool = False
    can_shell: bool = False
    can_file_write: bool = False
    can_diff: bool = False
    can_remote: bool = False
    can_replay: bool = False
    has_process_ids: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable snapshot."""
        return {k: v for k, v in self.__dict__.items()}


# ------------------------------------------------------------------
# Runtime identifiers
# ------------------------------------------------------------------

@dataclass
class RuntimeIds:
    """Opaque identifiers returned by get_runtime_ids().

    Concrete adapters populate the fields relevant to their backend.
    """
    session_id: Optional[str] = None
    process_id: Optional[str] = None
    remote_host: Optional[str] = None
    remote_pid: Optional[str] = None


# ------------------------------------------------------------------
# Transcript
# ------------------------------------------------------------------

@dataclass
class Transcript:
    """Captured stdout/stderr from a harness run."""
    stdout: str = ""
    stderr: str = ""


# ------------------------------------------------------------------
# Abstract base adapter
# ------------------------------------------------------------------

class BaseAdapter(ABC):
    """Abstract harness adapter.

    Each concrete adapter:
    - declares its ``adapter_type`` and ``capabilities``
    - manages a HarnessRun record in the product database
    - exposes lifecycle methods (start, stop, cancel)
    - returns runtime identifiers and transcript data
    """

    adapter_type: str = ""
    capabilities: AdapterCapabilities = field(default_factory=AdapterCapabilities)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # Abstract methods
    # ------------------------------------------------------------------

    @abstractmethod
    def start(
        self,
        *,
        workspace_id: str,
        session_id: str,
        command: str,
        task_spec_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Create a HarnessRun record and begin execution.

        Returns the ``harness_run_id`` of the newly created record.
        """

    @abstractmethod
    def stop(self, harness_run_id: str) -> None:
        """Graceful stop (e.g. SIGTERM)."""

    @abstractmethod
    def cancel(self, harness_run_id: str) -> None:
        """Forceful cancel (e.g. SIGKILL)."""

    @abstractmethod
    def get_runtime_ids(self, harness_run_id: str) -> RuntimeIds:
        """Return current runtime identifiers for a harness run."""

    @abstractmethod
    def get_transcript(self, harness_run_id: str) -> Transcript:
        """Return captured stdout/stderr for a harness run."""

    # ------------------------------------------------------------------
    # Concrete helpers
    # ------------------------------------------------------------------

    def capabilities_dict(self) -> Dict[str, Any]:
        """Return capabilities as a serialisable dict."""
        return self.capabilities.to_dict()
