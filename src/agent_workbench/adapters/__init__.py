"""Harness adapter layer for Agent Workbench.

This package also exposes the adapter **registry** (a small dispatch
table mapping ``harness_type`` to the concrete adapter class).  The
registry is intentionally defined here — not in ``web.runs`` — to
break a real circular import between :mod:`agent_workbench.web.runs`
and :mod:`agent_workbench.services.run_service` (the service wants to
resolve adapter classes without going through the web layer).
"""

from __future__ import annotations

from typing import Dict, Optional, Type

from agent_workbench.adapters.base import (
    AdapterCapabilities,
    BaseAdapter,
    HarnessAdapterError,
    HarnessNotReadyError,
    HarnessProcessError,
    RuntimeIds,
    Transcript,
)
from agent_workbench.adapters.discussion import DiscussionAdapter
from agent_workbench.adapters.hermes_adapter import HermesAdapter
from agent_workbench.adapters.opencode import OpencodeAdapter
from agent_workbench.adapters.permission import PermissionModel
from agent_workbench.adapters.shell import ShellAdapter
from agent_workbench.adapters.ssh import SshAdapter

__all__ = [
    "AdapterCapabilities",
    "BaseAdapter",
    "DiscussionAdapter",
    "HermesAdapter",
    "OpencodeAdapter",
    "PermissionModel",
    "ShellAdapter",
    "SshAdapter",
    "HarnessAdapterError",
    "HarnessNotReadyError",
    "HarnessProcessError",
    "RuntimeIds",
    "Transcript",
    "get_adapter_class",
    "register_adapter",
]


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------
#
# A small dispatch table mapping ``harness_type`` to the adapter class
# (not instance) that should handle start/stop/cancel.  The web layer
# and the service layer both consult this registry; keeping it here
# lets us avoid the historical circular import between
# :mod:`agent_workbench.web.runs` and :mod:`agent_workbench.services.run_service`.
#
# The five harness types defined in the product spec (06 §2):
#   discussion, hermes, opencode, shell, ssh
_ADAPTER_REGISTRY: Dict[str, Type[BaseAdapter]] = {}


def register_adapter(adapter_type: str, cls: Type[BaseAdapter]) -> None:
    """Register *cls* as the adapter for *adapter_type*."""
    _ADAPTER_REGISTRY[adapter_type] = cls


def get_adapter_class(harness_type: str) -> Optional[Type[BaseAdapter]]:
    """Return the adapter class registered for *harness_type* (lazy import)."""
    if not _ADAPTER_REGISTRY:
        # Lazy import so importing this module never costs the runtime
        # of every concrete adapter.
        register_adapter("discussion", DiscussionAdapter)
        register_adapter("hermes", HermesAdapter)
        register_adapter("opencode", OpencodeAdapter)
        register_adapter("shell", ShellAdapter)
        register_adapter("ssh", SshAdapter)
    return _ADAPTER_REGISTRY.get(harness_type)


# Eager registration at import-time would force every test that touches
# the package to instantiate the registry.  Keep it lazy.
