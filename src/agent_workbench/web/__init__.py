"""Web layer for the Agent Workbench.

Flask application factory and blueprints providing the product-layer UI
surface for channels, sessions, and routed messages.

The web layer is intentionally thin: it translates HTTP requests into calls
on the existing service layer (``OrchestratorService``, ``SessionService``,
``RoutingService``) and renders Jinja templates. All product semantics and
state live in the SQLite ``workbench.db`` database.
"""

from agent_workbench.web.app import create_app

__all__ = ["create_app"]
