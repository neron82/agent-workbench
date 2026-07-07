"""Chat UX bubble helpers.

Translates the internal ``RoutedMessage`` shape into user-facing
``bubble_role`` / ``bubble_initials`` / ``bubble_display_name`` /
``bubble_time`` values used by the message bubble template.

The point of keeping these in Python (not Jinja macros) is twofold:

1. They are easy to unit test in isolation — the route and the template
   share the same helpers, so the SSE frames, the polling-since JSON
   responses, and the synchronous ``/messages/<id>`` view all render
   consistently.
2. They are easy to mock from the *server-side* tests in
   ``tests/test_messages_sse.py`` without spinning up a full Jinja
   environment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping, Optional


# source_type values produced by the routing layer.
_AGENT_SOURCE_TYPES = frozenset({"agent", "orchestrator", "worker"})


def bubble_role(message) -> str:
    """Return the visual role bucket for a message.

    Mapping:

    * ``source_type == "user"``  -> ``"user"``
    * ``source_type in {agent, orchestrator, worker}`` -> ``"agent"``
    * ``message_kind == "system"`` or ``source_type == "system"`` -> ``"system"``
    * anything else -> ``"agent"`` (default bucket)
    """
    source_type = getattr(message, "source_type", None)
    if source_type == "user":
        return "user"
    if source_type == "system":
        return "system"
    if getattr(message, "message_kind", None) == "system":
        return "system"
    if source_type in _AGENT_SOURCE_TYPES:
        return "agent"
    return "agent"


def bubble_initials(message, participants: Optional[Mapping[str, str]] = None) -> str:
    """Return 1–2 character initials for the bubble avatar.

    * ``user``     -> ``"U"``
    * ``system``   -> ``"S"``
    * ``agent``    -> first letter of the resolved display name
      (e.g. ``"Atlas"`` -> ``"A"``). Falls back to ``"A"``.
    """
    role = bubble_role(message)
    if role == "user":
        return "U"
    if role == "system":
        return "S"
    name = bubble_display_name(message, participants) or "agent"
    name = name.strip()
    if not name:
        return "A"
    return name[0].upper()


def bubble_display_name(message, participants: Optional[Mapping[str, str]] = None) -> str:
    """Return a human-readable display name for a message's source.

    For agent/orchestrator/worker sources we look up ``source_id`` in the
    ``participants`` mapping (a ``{binding_id: agent_name}`` index). If
    nothing matches we fall back to ``source_id[:8]`` so the user sees
    *something* meaningful in the bubble.

    For user sources we return ``source_id`` (typically ``"web-user"``).

    For system sources we return ``"System"``.
    """
    source_type = getattr(message, "source_type", None) or ""
    source_id = getattr(message, "source_id", None) or ""
    if source_type == "user":
        return source_id or "user"
    if source_type == "system":
        return "System"

    # Agent-shaped sources. Try participants index first.
    participants = participants or {}
    if source_id and source_id in participants:
        return participants[source_id]
    if source_id:
        return source_id[:8] + ("…" if len(source_id) > 8 else "")
    return "agent"


def bubble_time(created_at) -> str:
    """Format a ``created_at`` float as ``HH:MM:SS`` in UTC.

    The format is intentionally compact and machine-parseable so it fits
    inside the bubble header without dominating it. The full ISO
    timestamp is also available on the ``<time datetime="...">``
    attribute for clients that want to show a tooltip / locale-aware
    time.
    """
    if created_at is None:
        return ""
    try:
        return datetime.fromtimestamp(float(created_at), UTC).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""
